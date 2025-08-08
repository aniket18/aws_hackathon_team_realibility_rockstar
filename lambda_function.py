import pandas as pd
import datetime
from jinja2 import Template
import boto3
import json
import io

# AWS Clients for S3, Textract and Bedrock
s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
bedrock_runtime = boto3.client(service_name='bedrock-runtime', region_name='us-east-1')

# Set S3 input/output bucket and paths
bucket_name = "cams-input-hackathon-bucket"
in_prefix = "CAMS/"
out_prefix = "output/"

# Helper to load CSVs from S3
def load_csv_from_s3(bucket, key):
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return pd.read_csv(io.BytesIO(response['Body'].read()))

# Formatting helpers
def format_currency(val):   
    return f"${val:,.2f}" if pd.notnull(val) else "N/A"

def format_percentage(val):
    return f"{val:.2f}%" if pd.notnull(val) else "N/A"

# Textract function to extract fields from a PDF in S3
def extract_from_textract(s3_bucket, s3_key):
    response = textract_client.analyze_document(
        Document={'S3Object': {'Bucket': s3_bucket, 'Name': s3_key}},
        FeatureTypes=["FORMS"]
    )
    fields = {}
    blocks = {block['Id']: block for block in response['Blocks']}
    for block in response['Blocks']:
        if block['BlockType'] == 'KEY_VALUE_SET' and 'KEY' in block['EntityTypes']:
            key_block = block
            val_block_id = next((rel['Ids'][0] for rel in key_block.get('Relationships', []) if rel['Type'] == 'VALUE'), None)
            key_text = ''
            val_text = ''
            if key_block.get('Relationships'):
                for rel in key_block['Relationships']:
                    if rel['Type'] == 'CHILD':
                        for cid in rel['Ids']:
                            key_text += next((b['Text'] for b in response['Blocks'] if b['Id'] == cid and 'Text' in b), '')
            if val_block_id:
                val_block = blocks.get(val_block_id)
                if val_block and val_block.get('Relationships'):
                    for rel in val_block['Relationships']:
                        if rel['Type'] == 'CHILD':
                            for cid in rel['Ids']:
                                val_text += next((b['Text'] for b in response['Blocks'] if b['Id'] == cid and 'Text' in b), '')
            fields[key_text.strip()] = val_text.strip()
    return fields

# Bedrock function to generate approval conditions
def generate_approval_conditions(cam_text):
    body = json.dumps({
        "messages": [
            {"role": "user", "content": f"Review the following CAM and generate approval conditions for a loan:\n\n{cam_text}\n\nApproval Conditions:"}
        ],
        "max_tokens": 300,
        "temperature": 0.5,
        "anthropic_version": "bedrock-2023-05-31"
    })

    response = bedrock_runtime.invoke_model(
        body=body,
        modelId="anthropic.claude-3-7-sonnet-20250219-v1:0",
        accept="application/json",
        contentType="application/json"
    )

    response_body = json.loads(response['body'].read())
    return response_body['content'][0]['text'].strip() if 'content' in response_body else "N/A"

# Main execution logic
def main():
    # Load all CSVs from S3
    clients = load_csv_from_s3(bucket_name, f"{in_prefix}clients.csv")
    loan_applications = load_csv_from_s3(bucket_name, f"{in_prefix}loan_applications.csv")
    borrowing_requests = load_csv_from_s3(bucket_name, f"{in_prefix}borrowing_requests.csv")
    underwriting_decisions = load_csv_from_s3(bucket_name, f"{in_prefix}underwriting_decisions.csv")
    credit_approval_memos = load_csv_from_s3(bucket_name, f"{in_prefix}credit_approval_memos.csv")
    term_sheets = load_csv_from_s3(bucket_name, f"{in_prefix}term_sheets.csv")

    # Merge key datasets
    df = loan_applications \
        .merge(clients, on="client_id", how="left") \
        .merge(borrowing_requests, on="application_id", how="left") \
        .merge(underwriting_decisions, on="application_id", how="left") \
        .merge(credit_approval_memos, on="application_id", how="left") \
        .merge(term_sheets, on="application_id", how="left")

    # Load all templates from JSON
    response = s3_client.get_object(Bucket=bucket_name, Key=f"{in_prefix}credit_agreement_templates.json")
    templates = json.loads(response['Body'].read().decode('utf-8'))
    template_map = {t["template_id"]: Template(t["template_content"]) for t in templates}

    # Default values
    defaults = {
        "agreement_date": datetime.date.today().strftime("%Y-%m-%d"),
        "lender_name": "Citi Private Bank",
        "payment_day": "1st",
        "grace_period": "15",
        "state_jurisdiction": "New York"
    }

    # Generate agreements
    output_rows = []
    for _, row in df.iterrows():
        template_id = row.get("template_id", "TEMPLATE-001")
        template = template_map.get(template_id)
        if not template:
            continue

        # Define field mapping logic for each template
        # Extend here to support TEMPLATE-002, TEMPLATE-003
        mapping = {
            "TEMPLATE-001": {
                "borrower_name": "full_name",
                "loan_amount": "requested_amount",
                "interest_rate": "recommended_rate",
                "loan_term": "term_years",
                "monthly_payment": "estimated_monthly_payment",
                "annual_income": "annual_income",
                "credit_score": "credit_score",
                "ltv_ratio": "ltv_ratio",
                "employment_status": "employment_status",
                "borrower_address": lambda row: f"{row['address_line1']}, {row['city']}, {row['state']} {row['zip_code']}",
                "property_address": "property_address",
                "property_value": "property_value",
                "down_payment": lambda row: row["property_value"] - row["requested_amount"] if pd.notnull(row["property_value"]) and pd.notnull(row["requested_amount"]) else "N/A"
            }
        }.get(template_id, {})

        context = {}
        for target_field, source in mapping.items():
            context[target_field] = source(row) if callable(source) else row.get(source, "N/A")

        # Apply transformations
        context["loan_amount"] = format_currency(context.get("loan_amount"))
        context["monthly_payment"] = format_currency(context.get("monthly_payment"))
        context["interest_rate"] = format_percentage(context.get("interest_rate"))
        context["ltv_ratio"] = format_percentage(context.get("ltv_ratio"))
        context["property_value"] = format_currency(context.get("property_value"))
        context["down_payment"] = format_currency(context.get("down_payment")) if context.get("down_payment") != "N/A" else "N/A"

        # Add default values
        context.update(defaults)

        # Textract + Bedrock integration
        try:
            cam_s3_key = f"cams/{row['application_id']}.pdf"
            textract_fields = extract_from_textract(bucket_name, cam_s3_key)
            cam_text = "\n".join([f"{k}: {v}" for k, v in textract_fields.items()])
            context["approval_conditions"] = generate_approval_conditions(cam_text)
        except Exception:
            context["approval_conditions"] = "Approval conditions not available."

        agreement = template.render(**context)
        output_rows.append({
            "application_id": row["application_id"],
            "client_id": row["client_id"],
            "template_id": template_id,
            "agreement_text": agreement
        })

    # Save results to S3
    output_df = pd.DataFrame(output_rows)
    csv_buffer = io.StringIO()
    output_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=bucket_name, Key=f"{out_prefix}populated_credit_agreements.csv", Body=csv_buffer.getvalue())

# Entry point
def lambda_handler(event, context):
    main()

