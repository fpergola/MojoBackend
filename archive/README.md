# HRRR backend: containerized ingest + API

This package turns the ingest side into a Lambda **container image** so you can bundle the GRIB decode stack. The API side stays lightweight.

## Architecture

- **Ingest Lambda** (container image)
  - Runs hourly from EventBridge
  - Reads NOAA HRRR GRIB2 from the public AWS Open Data bucket
  - Uses the `.idx` sidecar to byte-range fetch only `UGRD` and `VGRD` at `10 m above ground`
  - Decodes GRIB messages with `eccodes`
  - Crops/resamples to a compact regular grid
  - Writes JSON documents to S3 under:
    - `processed/wind/<validTime>.json`
    - `processed/wind/latest.json`
  - Also writes a **placeholder current field** under `processed/current/...`

- **API Lambda** (zip or inline)
  - `GET /health`
  - `GET /latest?type=wind|current`
  - `GET /field?type=wind|current&valid=<ISO8601>`

## Why a container image

AWS Lambda supports container images, and CloudFormation deploys them using `PackageType: Image` and `Code.ImageUri`. The image must live in Amazon ECR in the same Region as the Lambda function. citeturn0search0turn0search3turn0search8

The ingest side is the piece that benefits from a container because it needs the meteorological decode toolchain. Lambda also supports Python container images using AWS base images or alternative images with the Lambda runtime interface client. citeturn0search0turn0search1

NOAA publishes HRRR in public AWS buckets, including the main GRIB2 archive and an HRRR Zarr archive. citeturn0search2

## Build and push the ingest container

Pick a Region for your stack, for example `us-west-2`.

```bash
export AWS_REGION=us-west-2
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export REPO_NAME=mojo-weather-ingest

aws ecr create-repository --repository-name "$REPO_NAME" --region "$AWS_REGION"

export ECR_URI="$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO_NAME"

aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com"

docker build -t "$REPO_NAME:latest" ./ingest_lambda

docker tag "$REPO_NAME:latest" "$ECR_URI:latest"
docker push "$ECR_URI:latest"
```

## Deploy the stack

```bash
aws cloudformation deploy \
  --template-file template.yaml \
  --stack-name mojo-weather \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameter-overrides \
      ProjectName=mojo-weather \
      IngestImageUri="$ECR_URI:latest"
```

After deploy:
- use the `ApiUrl` output for the app
- invoke the ingest Lambda manually once if you want immediate data

## Manual ingest test

```bash
aws lambda invoke \
  --function-name mojo-weather-ingest \
  --payload '{"weather_types":["wind","current"],"bbox":"-123.40,47.20,-122.00,48.10","rows":80,"cols":120,"horizon_hours":6}' \
  out.json
cat out.json
```

## Output contract

Example wind document:

```json
{
  "schemaVersion": 1,
  "weatherType": "wind",
  "model": "HRRR",
  "runTime": "2026-03-09T12:00:00Z",
  "validTime": "2026-03-09T15:00:00Z",
  "field": {
    "rows": 80,
    "cols": 120,
    "bbox": {
      "minLat": 47.2,
      "maxLat": 48.1,
      "minLon": -123.4,
      "maxLon": -122.0
    },
    "u": [...],
    "v": [...],
    "units": "m/s",
    "gridType": "regular_latlon_resampled_from_hrrr"
  },
  "source": {
    "provider": "NOAA AWS Open Data",
    "bucket": "noaa-hrrr-bdp-pds",
    "key": "hrrr.20260309/conus/hrrr.t12z.wrfsfcf03.grib2",
    "domain": "conus",
    "product": "wrfsfc",
    "variables": ["UGRD", "VGRD"],
    "level": "10 m above ground"
  }
}
```

## Current data integration plan

The stack currently scaffolds **current** as a placeholder because HRRR is an atmospheric model, not an ocean-current model.

Recommended production approach:

1. Keep the **same field contract** for both wind and current:
   - `rows`, `cols`, `bbox`, `u`, `v`, `units`, `gridType`
2. Add a second ingest path for current from a real ocean model or NOAA current product.
3. Write outputs to:
   - `processed/current/<validTime>.json`
   - `processed/current/latest.json`
4. Let the app consume both with the same interpolation/rendering code.

Good candidates for current sources:
- NOAA OFS nowcast/forecast products
- HYCOM
- CMEMS, if your geography and licensing fit
- a local tide/current mesh if you want narrow-channel fidelity

For the app, the clean contract is:
- same JSON shape for wind and current
- same native bilinear interpolation
- separate styling/toggles
- optional land-mask later

## Notes

- The ingest resample is **nearest-neighbor** right now. Replace with vector-aware bilinear resampling for smoother rendering.
- HRRR native grid is not regular lat/lon. This package simplifies it into an app-friendly regular grid.
- If you prefer the HRRR Zarr archive instead of GRIB2, keep the same output contract and only swap the decoder path.
