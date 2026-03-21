# Mojo HRRR Backend on AWS

This document captures the working deployment path for the Mojo HRRR wind/current backend on AWS, including the fixes discovered during installation.

## What this backend deploys

The stack creates:

- An S3 bucket for processed weather fields
- A container-image Lambda for ingest (`IngestLambda`)
- A zip-based Python Lambda for the public API (`ApiLambda`)
- A Lambda Function URL for the API
- An hourly EventBridge schedule to trigger ingest

The forecast backend endpoints are:

- `/health`
- `/latest?type=wind`
- `/latest?type=current`
- `/field?type=wind&valid=...`
- `/field?type=current&valid=...`

---

## Prerequisites

Install locally:

- AWS CLI v2
- Docker Desktop
- Access to AWS services:
  - CloudFormation
  - ECR
  - Lambda
  - IAM
  - S3
  - EventBridge

Confirm AWS CLI works:

```bash
aws sts get-caller-identity
```

---

## 1. Configure AWS CLI

Run:

```bash
aws configure
```

Use values similar to:

- AWS Access Key ID: your key
- AWS Secret Access Key: your secret
- Default region name: `us-west-2`
- Default output format: `json`

Verify:

```bash
aws sts get-caller-identity
```

---

## 2. Unzip and enter the backend folder

```bash
unzip hrrr_backend_containerized.zip
cd hrrr_backend_containerized
```

---

## 3. Set environment variables

These are the values that worked.

```bash
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT_NAME=mojo-instruments-backend
export REPO_NAME=mojo-instruments-backend-ingest
export ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}"
```

Confirm them:

```bash
echo "$ACCOUNT_ID"
echo "$PROJECT_NAME"
echo "$REPO_NAME"
echo "$ECR_URI"
printf '%s\n' "$ECR_URI:latest"
```

The last line should look like:

```text
671232274101.dkr.ecr.us-west-2.amazonaws.com/mojo-instruments-backend-ingest:latest
```

---

## 4. Create the ECR repository

```bash
aws ecr create-repository \
  --repository-name "$REPO_NAME" \
  --region "$AWS_REGION"
```

If it already exists, that is fine.

---

## 5. Log Docker into ECR

```bash
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
```

---

## 6. Update `template.yaml` before deployment

These fixes were required.

### A. Lower Lambda memory

The account rejected `4096` MB. Set the ingest Lambda to `3008`.

Change:

```yaml
MemorySize: 4096
```

To:

```yaml
MemorySize: 3008
```

### B. Fix Function URL CORS methods

For `AWS::Lambda::Url`, `OPTIONS` is not valid in `Cors.AllowMethods`.

Change:

```yaml
AllowMethods:
  - GET
  - OPTIONS
```

To:

```yaml
AllowMethods:
  - GET
```

### C. Fix Lambda environment variable string values

Use string interpolation for numeric environment values:

```yaml
DEFAULT_ROWS: !Sub '${DefaultRows}'
DEFAULT_COLS: !Sub '${DefaultCols}'
DEFAULT_HORIZON_HOURS: !Sub '${DefaultHorizonHours}'
```

### D. Use the API Lambda ARN in the Function URL

Use:

```yaml
TargetFunctionArn: !GetAtt ApiLambda.Arn
```

### E. Fix the NOAA HRRR bucket name

Change:

```yaml
NOAA_HRRR_BUCKET: noaa-hrrr-bdp-pds
```

To:

```yaml
NOAA_HRRR_BUCKET: noaa-hrrr-pds
```

### F. Keep the EventBridge input JSON valid

The `Input` block under `IngestScheduleRule` should end with a single closing brace only.

Correct version:

```yaml
Input: !Sub |
  {
    "weather_types": ["wind", "current"],
    "bbox": "${DefaultBBox}",
    "rows": ${DefaultRows},
    "cols": ${DefaultCols},
    "horizon_hours": ${DefaultHorizonHours}
  }
```

### G. Simplify inline API code if needed

If you run into `Code: !If` property validation issues for `ApiLambda`, use a direct inline `Code.ZipFile` block instead of conditionally switching between inline zip and S3 source.

---

## 7. Validate the template

```bash
aws cloudformation validate-template --template-body file://template.yaml --region "$AWS_REGION"
```

If it prints JSON successfully, validation passed.

---

## 8. Build the Lambda container image in a Lambda-compatible format

This part mattered.

Use `buildx`, target `linux/amd64`, and disable provenance and SBOM so Lambda accepts the image.

```bash
docker rmi "$REPO_NAME:latest" || true
docker rmi "$ECR_URI:latest" || true

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  -t "$REPO_NAME:latest" \
  ./ingest_lambda \
  --load
```

Why this matters:

- Without `--platform linux/amd64`, image architecture may not match Lambda expectations on a Mac
- Without `--provenance=false`, Lambda may reject the manifest/media type produced by `buildx`

---

## 9. Tag and push the image

```bash
docker tag "$REPO_NAME:latest" "$ECR_URI:latest"

docker push "$ECR_URI:latest"
```

Verify the image exists in ECR:

```bash
aws ecr describe-images \
  --repository-name "$REPO_NAME" \
  --region "$AWS_REGION"
```

---

## 10. Delete any failed stack before redeploying

If the stack ended in `ROLLBACK_COMPLETE`, delete it before trying again.

```bash
aws cloudformation delete-stack \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION"

aws cloudformation wait stack-delete-complete \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION"
```

---

## 11. Deploy the stack

Use the literal image URI if you want to avoid any shell variable mishap.

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "$PROJECT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    IngestImageUri="$ECR_URI:latest"
```

Equivalent fully expanded example:

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name mojo-instruments-backend \
  --capabilities CAPABILITY_NAMED_IAM \
  --region us-west-2 \
  --parameter-overrides \
    ProjectName=mojo-instruments-backend \
    IngestImageUri=671232274101.dkr.ecr.us-west-2.amazonaws.com/mojo-instruments-backend-ingest:latest
```

---

## 12. Get stack outputs

```bash
aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs"
```

The useful outputs are:

- `ProcessedBucketName`
- `ApiUrl`
- `IngestFunctionName`

Get just the API URL:

```bash
aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text
```

---

## 13. Invoke ingest manually once

Do this immediately after deployment so the backend has data.

```bash
aws lambda invoke \
  --function-name "${PROJECT_NAME}-ingest" \
  --region "$AWS_REGION" \
  --payload '{"weather_types":["wind","current"],"bbox":"-123.40,47.20,-122.00,48.10","rows":80,"cols":120,"horizon_hours":6}' \
  out.json

cat out.json
```

---

## 14. Verify processed data in S3

First get the bucket name from outputs, then:

```bash
aws s3 ls s3://YOUR_BUCKET_NAME/processed/wind/ --region "$AWS_REGION"
aws s3 ls s3://YOUR_BUCKET_NAME/processed/current/ --region "$AWS_REGION"
```

---

## 15. Test the API

Set the API URL:

```bash
API_URL=$(aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text)
```

Health check:

```bash
curl "$API_URL/health"
```

Latest wind:

```bash
curl "$API_URL/latest?type=wind"
```

Latest current:

```bash
curl "$API_URL/latest?type=current"
```

Specific field:

```bash
curl "$API_URL/field?type=wind&valid=2026-03-09T15:00:00Z"
```

---

## 16. Troubleshooting notes from the actual deployment

### Error: repository does not exist / bad repo name

If you see a repo like:

```text
mojo-instruments-backend-ingestatest
```

that is wrong. The correct repository is:

```text
mojo-instruments-backend-ingest
```

and the correct image URI is:

```text
671232274101.dkr.ecr.us-west-2.amazonaws.com/mojo-instruments-backend-ingest:latest
```

### Error: `OPTIONS is not a valid enum value`

Remove `OPTIONS` from `ApiFunctionUrl -> Cors -> AllowMethods`.

### Error: Lambda memory must be less than or equal to 3008

Set:

```yaml
MemorySize: 3008
```

### Error: image manifest/media type is not supported

Rebuild with:

```bash
docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  -t "$REPO_NAME:latest" \
  ./ingest_lambda \
  --load
```

### Stack stuck in `ROLLBACK_COMPLETE`

Delete the stack before retrying:

```bash
aws cloudformation delete-stack --stack-name "$PROJECT_NAME" --region "$AWS_REGION"
aws cloudformation wait stack-delete-complete --stack-name "$PROJECT_NAME" --region "$AWS_REGION"
```

### Need the exact failing resource

Use:

```bash
aws cloudformation describe-stack-events \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --max-items 30
```

---

## 17. iOS app integration note

Once deployed, the iOS app should point its forecast base URL to the `ApiUrl` output from the stack.

Example format:

```text
https://abc123xyz.lambda-url.us-west-2.on.aws/
```

Use that as the base URL in the TideTrack / forecast client configuration.

---

## 18. Final recommended command sequence

```bash
aws configure

unzip hrrr_backend_containerized.zip
cd hrrr_backend_containerized

export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT_NAME=mojo-instruments-backend
export REPO_NAME=mojo-instruments-backend-ingest
export ECR_URI="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}"

aws ecr create-repository --repository-name "$REPO_NAME" --region "$AWS_REGION"
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

aws cloudformation validate-template --template-body file://template.yaml --region "$AWS_REGION"

docker buildx build \
  --platform linux/amd64 \
  --provenance=false \
  --sbom=false \
  -t "$REPO_NAME:latest" \
  ./ingest_lambda \
  --load

docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

aws cloudformation delete-stack --stack-name "$PROJECT_NAME" --region "$AWS_REGION" || true
aws cloudformation wait stack-delete-complete --stack-name "$PROJECT_NAME" --region "$AWS_REGION" || true

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "$PROJECT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    IngestImageUri="$ECR_URI:latest"

aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs"

aws lambda invoke \
  --function-name "${PROJECT_NAME}-ingest" \
  --region "$AWS_REGION" \
  --payload '{"weather_types":["wind","current"],"bbox":"-123.40,47.20,-122.00,48.10","rows":80,"cols":120,"horizon_hours":6}' \
  out.json

cat out.json
```
