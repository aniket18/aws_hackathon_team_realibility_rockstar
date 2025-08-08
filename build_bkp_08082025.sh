#!/bin/bash
set -e

echo "🔧 Starting Docker build for Lambda zip..."
echo "🗂️ Current directory: $(pwd)"
ls -lh

docker run --rm -v "$PWD":/var/task public.ecr.aws/sam/build-python3.12 \
/bin/bash -c "
    set -e
    echo '📥 Installing dependencies compatible with Lambda Python 3.12...'
    pip install --no-cache-dir pandas numpy jinja2 boto3 -t /var/task/package

    echo '📋 Copying lambda_function.py into package...'
    cp /var/task/lambda_function.py /var/task/package/

    echo '🗜️ Creating deployment package...'
    cd /var/task/package && zip -r9 /var/task/lambda_function.zip .

    echo '✅ Lambda package created successfully in /var/task'
"

# Final verification
if [[ -f lambda_function.zip ]]; then
    echo "✅ Build complete: lambda_function.zip is in $(pwd)"
else
    echo "❌ Build failed: lambda_function.zip not found."
    exit 1
fi

