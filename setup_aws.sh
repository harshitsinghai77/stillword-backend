# Run these once to create the DynamoDB tables and S3 bucket.

AWS_REGION="us-east-1"
BUCKET="stillword-app-content"

# ── S3 bucket ────────────────────────────────────────────────────────────────
aws s3api create-bucket \
  --bucket $BUCKET \
  --region $AWS_REGION

aws s3api put-public-access-block \
  --bucket $BUCKET \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"


# ── Users table ──────────────────────────────────────────────────────────────
# PK: email — direct GetItem on login/register, no GSI needed
aws dynamodb create-table \
  --table-name stillword-users \
  --attribute-definitions \
    AttributeName=email,AttributeType=S \
  --key-schema \
    AttributeName=email,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION


# ── Entries table ─────────────────────────────────────────────────────────────
# PK: user_id   SK: date (YYYY-MM-DD)
# Content is NOT stored here — it lives in S3
aws dynamodb create-table \
  --table-name stillword-entries \
  --attribute-definitions \
    AttributeName=user_id,AttributeType=S \
    AttributeName=date,AttributeType=S \
  --key-schema \
    AttributeName=user_id,KeyType=HASH \
    AttributeName=date,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region $AWS_REGION
