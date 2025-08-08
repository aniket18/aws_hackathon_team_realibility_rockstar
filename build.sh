#!/bin/bash
set -e

# 1️⃣ Remove old build artifacts
rm -rf package lambda_function.zip

# 2️⃣ Build in correct Python 3.12 Lambda environment
docker run --rm -v "$PWD":/var/task public.ecr.aws/sam/build-python3.12 \
/bin/bash -c "
    pip install --no-cache-dir --upgrade numpy pandas jinja2 boto3 -t /var/task/package && \
    cp /var/task/lambda_function.py /var/task/package/ && \
    cd /var/task/package && \
    zip -r9 /var/task/lambda_function.zip .
"

# 3️⃣ Confirm NumPy version matches runtime
unzip -l lambda_function.zip | grep numpy/core/_multiarray_umath.so && \
echo '✅ Zip built successfully with correct NumPy'

