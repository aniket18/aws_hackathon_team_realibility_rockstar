#!/bin/bash

echo "🔧 Starting Docker build for Lambda zip..."
echo "🗂️ Current working directory: $(pwd)"
echo "📄 Files in current directory:"
ls -lh

docker run --rm -v "$PWD":/opt amazonlinux:2 /bin/bash -c "
    set -e  # Exit on any error

    echo '📦 Step 1: Installing Python & required tools...'
    yum install -y python3 python3-pip zip || { echo '❌ Failed to install tools'; exit 1; }

    echo '📥 Step 2: Installing Python dependencies into /opt/package...'
    pip3 install pandas numpy jinja2 boto3 -t /opt/package || { echo '❌ Failed to install dependencies'; exit 1; }

    echo '📋 Step 3: Verifying lambda_function.py exists...'
    if [[ ! -f /opt/lambda_function.py ]]; then
        echo '❌ ERROR: lambda_function.py is missing in /opt'
        ls -lh /opt
        exit 1
    fi

    echo '📋 Step 4: Copying lambda_function.py to /opt/package...'
    cp /opt/lambda_function.py /opt/package/ || { echo '❌ Failed to copy lambda_function.py'; exit 1; }

    echo '🧳 Step 5: Listing contents of /opt/package before zipping...'
    ls -lh /opt/package

    echo '🗜️ Step 6: Creating zip file...'
    cd /opt/package && zip -r9 /opt/lambda_function.zip . || { echo '❌ Failed to create zip'; exit 1; }

    echo '✅ Lambda zip package created successfully inside container.'
"

# Final check
if [[ -f lambda_function.zip ]]; then
    echo "✅ Build complete: lambda_function.zip is available in $(pwd)"
else
    echo "❌ Zip file was not created. Please inspect logs above to identify the issue."
    echo "🔍 Listing files in current directory for verification:"
    ls -lh
fi

