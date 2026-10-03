import json
import os
import hashlib
import hmac
import uuid
import boto3
from datetime import datetime, timezone
from boto3.dynamodb.conditions import Key

dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
s3 = boto3.client("s3", region_name="us-east-1")

USERS_TABLE = os.environ["USERS_TABLE"]        
ENTRIES_TABLE = os.environ["ENTRIES_TABLE"]   
S3_BUCKET = os.environ["S3_BUCKET"]            
SECRET_KEY = os.environ["SECRET_KEY"]          

users_table = dynamodb.Table(USERS_TABLE)
entries_table = dynamodb.Table(ENTRIES_TABLE)


# helpers

def resp(status, body):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
        },
        "body": json.dumps(body),
    }


def hash_password(password):
    return hmac.new(SECRET_KEY.encode(), password.encode(), hashlib.sha256).hexdigest()


def s3_key(user_id, date):
    year, month, _ = date.split("-")
    return f"entries/{user_id}/{year}/{month}/{date}.txt"


def put_content(user_id, date, content):
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=s3_key(user_id, date),
        Body=content.encode("utf-8"),
        ContentType="text/plain",
    )


def get_content(user_id, date):
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=s3_key(user_id, date))
        return obj["Body"].read().decode("utf-8")
    except Exception:
        return ""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# routes

def register(body):
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    name = (body.get("name") or "").strip()

    if not email or not password:
        return resp(400, {"error": "Email and password are required"})

    # Direct GetItem — email is PK, no GSI needed
    existing = users_table.get_item(
        Key={"email": email},
        ProjectionExpression="email",
    ).get("Item")

    if existing:
        return resp(400, {"error": "An account with this email already exists"})

    user_id = "usr_" + uuid.uuid4().hex[:12]
    users_table.put_item(Item={
        "email": email,
        "user_id": user_id,
        "password_hash": hash_password(password),
        "name": name,
        "target_words": 750,
        "theme": "oatmeal",
        "created_at": now_iso(),
    })

    # Migrate guest localEntries — write content to S3, metadata to DynamoDB
    local_entries = body.get("localEntries") or []
    migrated = []
    for item in local_entries:
        date = item.get("date")
        if not date:
            continue
        put_content(user_id, date, item.get("content", ""))
        entries_table.put_item(
            Item={
                "user_id": user_id,
                "date": date,
                "word_count": int(item.get("wordCount", 0)),
                "completed": bool(item.get("completed", False)),
                "completed_at": item.get("completedAt"),
                "updated_at": item.get("updatedAt") or now_iso(),
            }
        )
        migrated.append({
            "date": date,
            "wordCount": int(item.get("wordCount", 0)),
            "completed": bool(item.get("completed", False)),
            "completedAt": item.get("completedAt"),
            "updatedAt": item.get("updatedAt") or now_iso(),
        })

    return resp(200, {
        "user": {
            "id": user_id,
            "email": email,
            "name": name,
            "targetWords": 750,
            "theme": "oatmeal",
            "isRegistered": True,
        },
        "entries": migrated,
    })


def login(body):
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""

    if not email or not password:
        return resp(400, {"error": "Email and password are required"})

    # Direct GetItem — email is PK
    user = users_table.get_item(
        Key={"email": email},
        ProjectionExpression="email, user_id, #n, target_words, theme, password_hash",
        ExpressionAttributeNames={"#n": "name"},
    ).get("Item")

    if not user or user["password_hash"] != hash_password(password):
        return resp(401, {"error": "Invalid email or password"})

    entries = _get_user_entries(user["user_id"])

    return resp(200, {
        "user": {
            "id": user["user_id"],
            "email": user["email"],
            "name": user.get("name", ""),
            "targetWords": int(user.get("target_words", 750)),
            "theme": user.get("theme", "oatmeal"),
            "isRegistered": True,
        },
        "entries": entries,
    })


def sync_entry(body):
    user_id = body.get("userId") or body.get("user_id")
    entry = body.get("entry")

    if not user_id or not entry or not entry.get("date"):
        return resp(400, {"error": "userId and entry.date are required"})

    date = entry["date"]
    content = entry.get("content", "")
    word_count = int(entry.get("wordCount", 0))
    completed = bool(entry.get("completed", False))
    completed_at = entry.get("completedAt") or (now_iso() if completed else None)
    updated_at = entry.get("updatedAt") or now_iso()

    # Only overwrite if incoming is newer
    existing = entries_table.get_item(
        Key={"user_id": user_id, "date": date},
        ProjectionExpression="updated_at",
    ).get("Item")

    if existing and updated_at < existing.get("updated_at", ""):
        return resp(200, {"success": True, "skipped": True})

    put_content(user_id, date, content)

    entries_table.put_item(Item={
        "user_id": user_id,
        "date": date,
        "word_count": word_count,
        "completed": completed,
        "completed_at": completed_at,
        "updated_at": updated_at,
    })

    return resp(200, {"success": True})


def get_entries(user_id):
    entries = _get_user_entries(user_id)
    return resp(200, {"entries": entries})


def get_entry_content(user_id, date):
    item = entries_table.get_item(
        Key={"user_id": user_id, "date": date},
        ProjectionExpression="word_count, completed, completed_at, updated_at",
    ).get("Item")

    if not item:
        return resp(404, {"error": "Entry not found"})

    content = get_content(user_id, date)
    return resp(200, {
        "date": date,
        "content": content,
        "wordCount": int(item.get("word_count", 0)),
        "completed": bool(item.get("completed", False)),
        "completedAt": item.get("completed_at"),
        "updatedAt": item.get("updated_at"),
    })


def update_profile(body):
    user_id = body.get("userId") or body.get("user_id")
    email = (body.get("email") or "").strip().lower()

    if not email:
        return resp(400, {"error": "email is required"})

    expr_parts = []
    values = {}
    names = {}

    if "name" in body:
        expr_parts.append("#n = :n")
        names["#n"] = "name"
        values[":n"] = body["name"].strip()
    if "targetWords" in body:
        expr_parts.append("target_words = :tw")
        values[":tw"] = int(body["targetWords"])
    if "theme" in body:
        expr_parts.append("theme = :th")
        values[":th"] = body["theme"]

    if not expr_parts:
        return resp(200, {"success": True})

    kwargs = {
        "Key": {"email": email},
        "UpdateExpression": "SET " + ", ".join(expr_parts),
        "ExpressionAttributeValues": values,
    }
    if names:
        kwargs["ExpressionAttributeNames"] = names

    users_table.update_item(**kwargs)
    return resp(200, {"success": True})


# internal

def _get_user_entries(user_id):
    result = entries_table.query(
        KeyConditionExpression=Key("user_id").eq(user_id),
        ProjectionExpression="#d, word_count, completed, completed_at, updated_at",
        ExpressionAttributeNames={"#d": "date"},
    )
    return [
        {
            "date": item["date"],
            "wordCount": int(item.get("word_count", 0)),
            "completed": bool(item.get("completed", False)),
            "completedAt": item.get("completed_at"),
            "updatedAt": item.get("updated_at"),
        }
        for item in result.get("Items", [])
    ]


# router

def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = event.get("rawPath", "/")
    body = {}

    if event.get("body"):
        try:
            raw = event["body"]
            if event.get("isBase64Encoded"):
                import base64
                raw = base64.b64decode(raw).decode("utf-8")
            body = json.loads(raw)
        except Exception:
            return resp(400, {"error": "Invalid JSON body"})

    if method == "OPTIONS":
        return {"statusCode": 200, "body": ""}

    if method == "GET" and path == "/ping":
        return {"statusCode": 200, "body": "Hi, I'm working"}

    if method == "POST" and path == "/auth/register":
        return register(body)

    if method == "POST" and path == "/auth/login":
        return login(body)

    if method == "POST" and path == "/entry":
        return sync_entry(body)

    if method == "GET" and path.startswith("/entries/"):
        user_id = path.split("/entries/", 1)[1].split("/")[0]
        return get_entries(user_id)

    if method == "GET" and path.startswith("/entry/"):
        parts = path.split("/")  # ['', 'entry', user_id, date]
        if len(parts) == 4:
            return get_entry_content(parts[2], parts[3])

    if method == "POST" and path == "/user/profile":
        return update_profile(body)

    return resp(404, {"error": "Not found"})
