# 1.Prerequisites on your machine

You’ll want these installed and configured locally:

AWS CLI

Docker Desktop

permission in AWS to use:

ECR

Lambda

CloudFormation

IAM

S3

EventBridge

Then confirm your AWS identity works:

aws sts get-caller-identity

Also choose the AWS region you want. Your package README uses us-west-2, which is a good default.

3. Unzip and move into the project folder
unzip hrrr_backend_containerized.zip
cd hrrr_backend_containerized

You should see:

README.md
template.yaml
api_lambda/
ingest_lambda/
4. Set environment variables

Run:

export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT_NAME=mojo-weather
export REPO_NAME=mojo-weather-ingest
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME

You can change PROJECT_NAME if you want a different stack/function naming prefix.

5. Create the ECR repository

This is where the ingest Lambda container image will live.

aws ecr create-repository \
  --repository-name "$REPO_NAME" \
  --region "$AWS_REGION"

If it already exists, AWS will say so. That is fine.

6. Log Docker into ECR
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"
7. Build the ingest Lambda image

From the folder that contains ingest_lambda/:

docker build -t "$REPO_NAME:latest" ./ingest_lambda

This image includes:

Python 3.12

eccodes

numpy

requests

awslambdaric

That is the heavy GRIB decode side of the backend.

8. Tag and push the image to ECR
docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

At this point the ingest Lambda image is in AWS and ready for CloudFormation to reference.

9. Deploy the CloudFormation stack

Run:

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "$PROJECT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    IngestImageUri="$ECR_URI:latest"

That should create:

S3 processed-weather bucket

IAM roles

ingest Lambda

API Lambda

Lambda Function URL

EventBridge hourly trigger

Optional parameters you may also want

If you want to override defaults during deploy:

aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "$PROJECT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    IngestImageUri="$ECR_URI:latest" \
    DefaultBBox="-123.40,47.20,-122.00,48.10" \
    DefaultRows=80 \
    DefaultCols=120 \
    DefaultHorizonHours=18 \
    EnableCurrentScaffold=true \
    PublicApiCorsAllowOrigin="*"
10. Get the outputs

After deploy completes:

aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs"

You should get outputs like:

ProcessedBucketName

ApiUrl

IngestFunctionName

You can also pull just the API URL:

aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='ApiUrl'].OutputValue" \
  --output text

Save that URL for the app.

11. Run the ingest manually once

The hourly schedule will populate data eventually, but it is better to seed it immediately.

First get the function name if needed:

aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='IngestFunctionName'].OutputValue" \
  --output text

Then invoke it:

aws lambda invoke \
  --function-name "${PROJECT_NAME}-ingest" \
  --region "$AWS_REGION" \
  --payload '{"weather_types":["wind","current"],"bbox":"-123.40,47.20,-122.00,48.10","rows":80,"cols":120,"horizon_hours":6}' \
  out.json

Then inspect the response:

cat out.json

You want to see ok: true or at least some successful results.

12. Verify files landed in S3

List the processed files:

aws s3 ls s3://YOUR_BUCKET_NAME/processed/wind/ --region "$AWS_REGION"
aws s3 ls s3://YOUR_BUCKET_NAME/processed/current/ --region "$AWS_REGION"

Or first get the bucket name from the stack outputs.

You should see files like:

processed/wind/latest.json

timestamped forecast files

processed/current/latest.json

Note that current is a placeholder scaffold in this package, not real ocean current data yet.

13. Test the API

Assume your function URL is stored in API_URL.

Health check
curl "$API_URL/health"
Latest wind
curl "$API_URL/latest?type=wind"
Latest current
curl "$API_URL/latest?type=current"
Specific field by valid time
curl "$API_URL/field?type=wind&valid=2026-03-09T15:00:00Z"

If /latest works but /field does not, usually it means that exact valid key does not exist in S3.

14. If CloudFormation fails, check these first

The most likely issues with this package are:

A. Template formatting issue

Fix the extra } in the EventBridge Input block.

B. Docker image architecture mismatch

Your template explicitly sets:

Architectures:
  - x86_64

So when building on Apple Silicon, it is safer to build for linux/amd64:

docker buildx build --platform linux/amd64 -t "$REPO_NAME:latest" ./ingest_lambda --load
docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

This is a very relevant one on a Mac.

C. IAM permission issues

Your AWS user/role needs permission for:

ecr:* enough to create repo and push image

cloudformation:* enough to deploy stack

iam:CreateRole, iam:PassRole, iam:AttachRolePolicy

lambda:*

s3:* at least bucket creation and object access

events:*

D. Lambda timeout or decode failures

The ingest Lambda is set to:

Timeout: 900

MemorySize: 4096

That is generous, but if HRRR fetch/decode fails, check CloudWatch logs:

aws logs describe-log-groups --region "$AWS_REGION"

Then inspect the ingest function’s log stream in CloudWatch.

15. One package detail you should know

Your template.yaml currently uses inline API code by default unless you pass ApiCodeS3Bucket and ApiCodeS3Key.

That means:

the stack will not automatically use api_lambda/index.py

instead it uses the inline ZipFile block inside template.yaml

That is fine for now, but it means if you edit api_lambda/index.py, nothing changes unless you also package and upload that code to S3 and deploy with those parameters.

So the simplest installation path is:

use the template as-is

rely on inline API code

only build/push the ingest container

16. Recommended exact install sequence

Use this order:

# 1. unzip and enter folder
unzip hrrr_backend_containerized.zip
cd hrrr_backend_containerized

# 2. fix template.yaml extra brace

# 3. set env vars
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export PROJECT_NAME=mojo-weather
export REPO_NAME=mojo-weather-ingest
export ECR_URI=$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME

# 4. create repo
aws ecr create-repository --repository-name "$REPO_NAME" --region "$AWS_REGION"

# 5. login to ECR
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

# 6. build amd64 image on Mac
docker buildx build --platform linux/amd64 -t "$REPO_NAME:latest" ./ingest_lambda --load

# 7. push image
docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"

# 8. deploy stack
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name "$PROJECT_NAME" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$AWS_REGION" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    IngestImageUri="$ECR_URI:latest"

# 9. get outputs
aws cloudformation describe-stacks \
  --stack-name "$PROJECT_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs"

# 10. run ingest once
aws lambda invoke \
  --function-name "${PROJECT_NAME}-ingest" \
  --region "$AWS_REGION" \
  --payload '{"weather_types":["wind","current"],"bbox":"-123.40,47.20,-122.00,48.10","rows":80,"cols":120,"horizon_hours":6}' \
  out.json
cat out.json
17. My recommendation for your case

For your first install, do this:

deploy in us-west-2

build with --platform linux/amd64

keep the inline API code

keep current scaffold enabled

manually invoke ingest right after deploy

test /health and /latest?type=wind

That will get you to a working backend fastest.

I can also turn this into a copy-paste deployment script for your Mac, plus a corrected template.yaml, so you can run it in one go.