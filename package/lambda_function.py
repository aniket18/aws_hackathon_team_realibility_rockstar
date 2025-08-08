import pandas as pd
import datetime
from jinja2 import Template
import boto3
import json
import io
import uuid

# AWS Clients
s3_client = boto3.client('s3')
textract_client = boto3.client('textract')
bedrock_runtime = boto3.client(service_name='bedrock-runtime', region_name='us-east-1')

# S3 paths
bucket_name = "cams-input-hackathon-bucket"
in_prefix = "CAMS/"
out_prefix = "output/"

# ---------- Helper Functions ----------
def load_csv_from_s3(bucket, key):
    response = s3_client.get_object(Bucket=bucket, Key=key)
    return pd.read_csv(io.BytesIO(response['Body'].read()))

def format_currency(val):
    try:
        return f"${float(val):,.2f}" if pd.notnull(val) else "N/A"
    except (ValueError, TypeError):
        return "N/A"

def format_percentage(val):
    try:
        return f"{float(val):.2f}%" if pd.notnull(val) else "N/A"
    except (ValueError, TypeError):
        return "N/A"

def extract_from_textract(s3_bucket, s3_key):
    response = textract_client.analyze_document(
        Document={'S3Object': {'Bucket': s3_bucket, 'Name': s3_key}},
        FeatureTypes=["FORMS"]
    )
    fields = {}
    blocks = {block['Id']: block for block in response['Blocks']}
    for block in response['Blocks']:
        if block['BlockType'] == 'KEY_VALUE_SET' and 'KEY' in block['EntityTypes']:
            key_text, val_text = '', ''
            val_block_id = next((rel['Ids'][0] for rel in block.get('Relationships', []) if rel['Type'] == 'VALUE'), None)
            if block.get('Relationships'):
                for rel in block['Relationships']:
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

# ---------- Main ----------
def main():
    # Load CSVs
    clients = load_csv_from_s3(bucket_name, f"{in_prefix}clients.csv")
    loan_applications = load_csv_from_s3(bucket_name, f"{in_prefix}loan_applications.csv")
    borrowing_requests = load_csv_from_s3(bucket_name, f"{in_prefix}borrowing_requests.csv")
    underwriting_decisions = load_csv_from_s3(bucket_name, f"{in_prefix}underwriting_decisions.csv")
    credit_approval_memos = load_csv_from_s3(bucket_name, f"{in_prefix}credit_approval_memos.csv")
    term_sheets = load_csv_from_s3(bucket_name, f"{in_prefix}term_sheets.csv")

    # Merge safely — drop duplicate join keys first
    df = loan_applications \
        .merge(clients, on="client_id", how="left") \
        .merge(borrowing_requests.drop(columns=['client_id', 'requested_amount'], errors='ignore'),
               on="application_id", how="left") \
        .merge(underwriting_decisions.drop(columns=['client_id', 'requested_amount'], errors='ignore'),
               on="application_id", how="left") \
        .merge(credit_approval_memos.drop(columns=['client_id', 'requested_amount'], errors='ignore'),
               on="application_id", how="left") \
        .merge(term_sheets.drop(columns=['client_id', 'requested_amount'], errors='ignore'),
               on="application_id", how="left")

    # Load templates
    templates_json = s3_client.get_object(Bucket=bucket_name, Key=f"{in_prefix}credit_agreement_templates.json")
    templates = json.loads(templates_json['Body'].read().decode('utf-8'))
    template_map = {t["template_id"]: {"template": Template(t["template_content"]), "name": t.get("template_name", "")}
                    for t in templates}

    defaults = {
        "agreement_date": datetime.date.today().strftime("%Y-%m-%d"),
        "lender_name": "Citi Private Bank",
        "payment_day": "1st",
        "grace_period": "15",
        "state_jurisdiction": "New York"
    }

    # Template field mappings
    template_mappings = {
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
            "borrower_address": lambda r: f"{r['address_line1']}, {r['city']}, {r['state']} {r['zip_code']}",
            "property_address": "property_address",
            "property_value": "property_value",
            "down_payment": lambda r: float(r["property_value"]) - float(r["requested_amount"])
            if pd.notnull(r["property_value"]) and pd.notnull(r["requested_amount"]) else "N/A"
        },
        "TEMPLATE-002": {
            "borrower_name": "full_name",
            "loan_amount": "approved_amount",
            "interest_rate": "final_rate",
            "loan_term": "term_months",
            "monthly_payment": "monthly_installment",
            "annual_income": "annual_income",
            "credit_score": "credit_score",
            "loan_purpose": "loan_purpose"
        },
        "TEMPLATE-003": {
            "borrower_name": "full_name",
            "loan_amount": "requested_amount",
            "interest_rate": "interest_percent",
            "loan_term": "loan_duration_years",
            "monthly_payment": "monthly_repayment",
            "collateral_details": "collateral_details"
        }
    }

    output_rows = []
    for _, row in df.iterrows():
        template_id = row["template_id"] if pd.notnull(row.get("template_id")) else "TEMPLATE-001"
        template_info = template_map.get(template_id)
        if not template_info:
            continue

        mapping = template_mappings.get(template_id, {})
        context = {field: (src(row) if callable(src) else row.get(src, "N/A")) for field, src in mapping.items()}

        # Apply formatting
        for f in ["loan_amount", "monthly_payment", "property_value", "down_payment"]:
            if f in context:
                context[f] = format_currency(context[f])
        for f in ["interest_rate", "ltv_ratio"]:
            if f in context:
                context[f] = format_percentage(context[f])

        context.update(defaults)

        # CAM extraction fix
        cam_text = None
        if pd.notnull(row.get("cam_content")):
            cam_text = row["cam_content"]
        elif pd.notnull(row.get("cam_s3_key")):
            try:
                textract_fields = extract_from_textract(bucket_name, row["cam_s3_key"])
                cam_text = "\n".join([f"{k}: {v}" for k, v in textract_fields.items()])
            except Exception as e:
                print(f"[WARN] Textract extraction failed for {row.get('application_id')}: {e}")

        if cam_text:
            context["approval_conditions"] = generate_approval_conditions(cam_text)
        else:
            context["approval_conditions"] = "Approval conditions not available."

        # Render agreement
        agreement_text = template_info["template"].render(**context)
        

        # Log the populated agreement content
        print(f"[DEBUG] Agreement for application_id={row['application_id']}: {agreement_text[:300]}...")

        output_rows.append({
            "agreement_id": str(uuid.uuid4()),  # runtime generated
            "cam_id": row.get("cam_id", "N/A"),
            "application_id": row["application_id"],
            "client_id": row["client_id"],
            "client_name": row.get("full_name", "N/A"),
            "template_id": template_id,
            "template_name": template_info["name"],
            "loan_type": row.get("loan_type", "N/A"),
            "loan_amount": context.get("loan_amount", "N/A"),
            "populated_agreement_content": agreement_text,
            "generation_date": defaults["agreement_date"],
            "status": "Generated",
            "review_required": "Yes",
            "compliance_check": "Pending"
        })

    # Save output
    output_df = pd.DataFrame(output_rows)
    csv_buffer = io.StringIO()
    output_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=bucket_name, Key=f"{out_prefix}populated_credit_agreements.csv",
                         Body=csv_buffer.getvalue())

# Lambda handler
def lambda_handler(event, context):
    main()
