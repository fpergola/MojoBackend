import boto3
import json
import os

s3 = boto3.client('s3')
BUCKET = os.environ['OUTPUT_BUCKET']
CORS = os.environ.get('CORS_ALLOW_ORIGIN', '*')


def response(code, body, content_type='application/json'):
    return {
        'statusCode': code,
        'headers': {
            'Content-Type': content_type,
            'Access-Control-Allow-Origin': CORS,
            'Access-Control-Allow-Methods': 'GET,OPTIONS',
            'Access-Control-Allow-Headers': 'content-type'
        },
        'body': body if isinstance(body, str) else json.dumps(body)
    }


def get_json(key):
    obj = s3.get_object(Bucket=BUCKET, Key=key)
    return json.loads(obj['Body'].read())


def handler(event, context):
    method = (event.get('requestContext', {}).get('http', {}).get('method')
              or event.get('httpMethod') or 'GET')
    if method == 'OPTIONS':
        return response(200, '')

    path = event.get('rawPath') or event.get('path') or '/'
    qs = event.get('queryStringParameters') or {}

    if path == '/health':
        return response(200, {'ok': True, 'bucket': BUCKET})

    if path == '/latest':
        weather_type = qs.get('type', 'wind')
        latest = get_json(f'processed/{weather_type}/latest.json')
        return response(200, latest)

    if path == '/field':
        weather_type = qs.get('type', 'wind')
        valid = qs.get('valid')
        key = qs.get('key') or (f'processed/{weather_type}/{valid}.json' if valid else None)
        if not key:
            return response(400, {'error': 'Provide valid or key'})
        return response(200, get_json(key))

    return response(404, {'error': 'Not found'})
