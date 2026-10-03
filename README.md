# Stillword — Backend

This is the backend for [Stillword](https://github.com/harshitsinghai77/stillword), a minimal daily writing app. It is a single AWS Lambda function written in Python, exposed via a Lambda Function URL, with no traditional server or framework involved.

All user auth, entry metadata, and cloud sync for the frontend are handled here. The frontend is a separate repository and calls this backend directly from the browser.

---

## Overview

- Single Lambda function that routes requests internally — no API Gateway, no Express, no framework
- Auth is email and password based, passwords are hashed with HMAC-SHA256
- Writing content is stored in S3 as plain text files, one per user per day
- Entry metadata (word count, streak data, timestamps) is stored in DynamoDB separately from content
- Guest users on the frontend are localStorage-only and never touch this backend. When a guest registers, their local entries are migrated to S3 and DynamoDB in a single request
- No CORS headers in code — handled entirely by Lambda Function URL configuration

---

## Architecture

Frontend hits a single Lambda Function URL. The Lambda routes requests internally to DynamoDB and S3.

- DynamoDB `stillword-users` — auth and profile
- DynamoDB `stillword-entries` — entry metadata and streak data
- S3 `stillword-content` — actual writing content, one file per day per user

---

## DynamoDB: stillword-users

PK is `email` — direct GetItem on login and register, no GSI needed.

| Attribute     | Type   | Notes                       |
|---------------|--------|-----------------------------|
| email         | String | PK                          |
| user_id       | String | usr_ + 12 char hex          |
| password_hash | String | HMAC-SHA256                 |
| name          | String |                             |
| target_words  | Number | default 750                 |
| theme         | String | oatmeal / sage / ink / pure |
| created_at    | String | ISO 8601                    |

## DynamoDB: stillword-entries

PK is `user_id`, SK is `date`. Always queried by user_id so no GSI needed. Content is never stored here.

| Attribute    | Type    | Notes                  |
|--------------|---------|------------------------|
| user_id      | String  | PK                     |
| date         | String  | SK — YYYY-MM-DD        |
| word_count   | Number  |                        |
| completed    | Boolean | true if >= target_words |
| completed_at | String  | ISO 8601 or null       |
| updated_at   | String  | ISO 8601               |

## S3: stillword-content

One plain text file per user per day at `entries/<user_id>/<YYYY>/<MM>/<date>.txt`. Overwritten on every save, no history kept. Only fetched when the user clicks a specific day in the calendar.

---

## API Routes

| Method | Path                      | Description                       |
|--------|---------------------------|-----------------------------------|
| POST   | /auth/register            | Create account, migrates guest entries |
| POST   | /auth/login               | Login, returns all entry metadata |
| POST   | /entry                    | Save or update an entry           |
| GET    | /entries/<user_id>        | All entry metadata, no content    |
| GET    | /entry/<user_id>/<date>   | Single entry with content from S3 |
| POST   | /user/profile             | Update name, theme, targetWords   |

---

## DB Call Count

- Login and register — 2 calls (users table + entries table)
- Every other route — 1 call
- Content reads and writes — 1 S3 call, no DynamoDB

---

## Setup Steps

### 1. Create S3 bucket

Run these in PowerShell after setting `$env:AWS_PROFILE = "your-profile"`.

- `aws s3api create-bucket --bucket stillword-content --region us-east-1`
- `aws s3api put-public-access-block --bucket stillword-content --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"`

### 2. Create DynamoDB tables

- `aws dynamodb create-table --table-name stillword-users --attribute-definitions AttributeName=email,AttributeType=S --key-schema AttributeName=email,KeyType=HASH --billing-mode PAY_PER_REQUEST --region us-east-1`
- `aws dynamodb create-table --table-name stillword-entries --attribute-definitions AttributeName=user_id,AttributeType=S AttributeName=date,AttributeType=S --key-schema AttributeName=user_id,KeyType=HASH AttributeName=date,KeyType=RANGE --billing-mode PAY_PER_REQUEST --region us-east-1`

### 3. Create the Lambda function

1. Go to AWS Lambda → Create function
2. Name: stillword-api
3. Runtime: Python 3.12
4. Architecture: x86_64
5. Click Create function

### 4. Upload the code

1. Open the Code tab
2. Paste the contents of lambda_function.py into the inline editor
3. Click Deploy

### 5. Set environment variables

Go to Configuration → Environment variables and add:

| Key           | Value                           |
|---------------|---------------------------------|
| USERS_TABLE   | stillword-users                 |
| ENTRIES_TABLE | stillword-entries               |
| S3_BUCKET     | stillword-content               |
| SECRET_KEY    | any long random string you pick |

### 6. Attach IAM permissions

1. Go to Configuration → Permissions → Execution role → click the role name
2. Add permissions → Create inline policy → JSON tab
3. Paste the contents of iam_policy.json
4. Name it stillword-lambda-policy and save

### 7. Enable Function URL

1. Go to Configuration → Function URL → Create function URL
2. Auth type: NONE
3. Copy the Function URL

### 8. Wire the frontend

Copy .env.example to .env and set VITE_LAMBDA_URL to your Function URL.

### 9. Set timeout

Configuration → General configuration → Timeout → set to 10 seconds.
