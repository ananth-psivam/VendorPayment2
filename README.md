# Vendor Payment Inquiry Reader

Reads PDF/HTML vendor documents from Supabase Storage, detects payment inquiries, extracts invoice numbers, validates status in Supabase, and drafts reply emails.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app/main.py
```

## Configure secrets

Create `app/.streamlit/secrets.toml` from the example and fill:

- SUPABASE_URL
- SUPABASE_ANON_KEY
- BUCKET_NAME
- Optional: BUCKET_PREFIX, SMTP_*

Ensure Supabase table `invoices` exists as per `supabase/schema.sql`.
