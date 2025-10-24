create table if not exists public.invoices (
  Supplier_Name text,
  Invoice_Date text,
  Total_Invoice_Amount text,
  Currency text default 'USD',
  Status text check (Status in ('Paid','Unpaid','Queued','On Hold','Rejected','Not Found')),
  Supplier_Invoice_No text,
  Comments text,
  Supplier_Invoice_Date date,
  file_url text
);
