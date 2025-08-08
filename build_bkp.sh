#!/bin/bash

# Exit on any error
set -e

# Clean up previous builds
rm -rf package lambda_function.zip

# Copy lambda_function.py to current folder if needed
if [ ! -f lambda_function.py ]; then
    echo "❌ lambda_function.py not found in $(pwd)"
    exit 1
fi

# Build in Docker
docker run --rm -v "$PWD":/var/task amazonlinux:2 /bin/bash -c "
    yum install -y python3 python3-pip zip && \
    pip3 install pandas numpy jinja2 boto3 -t package && \
    cp /var/task/lambda_function.py package/ && \
    cd package && \
    zip -r9 /var/task/lambda_function.zip .
"

echo "✅ Build complete: lambda_function.zip created in $(pwd)"


# Verify file presence
if [ -f "lambda_function.zip" ]; then
    echo "✅ Zip created at: $(pwd)/lambda_function.zip"
else
    echo "❌ Zip file not created. Check above logs for issues."
fi
