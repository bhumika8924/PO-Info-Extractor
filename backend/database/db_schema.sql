CREATE TABLE IF NOT EXISTS po_headers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    file_name VARCHAR(255),
    po_number VARCHAR(100),
    po_date VARCHAR(50),
    buyer_name TEXT,
    billing_address TEXT,
    billing_state VARCHAR(100),
    billing_pincode VARCHAR(20),
    billing_gst_number VARCHAR(50),
    vendor_name TEXT,
    vendor_gst_number VARCHAR(50),
    total_amount DECIMAL(15,2),
    extraction_status VARCHAR(50),
    warnings TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS po_items (
    id INT AUTO_INCREMENT PRIMARY KEY,
    file_name VARCHAR(255),
    po_number VARCHAR(100),
    item_no VARCHAR(50),
    item_name TEXT,
    item_description TEXT,
    hsn_sac VARCHAR(100),
    quantity VARCHAR(50),
    uom VARCHAR(50),
    unit_price VARCHAR(50),
    tax_percent VARCHAR(50),
    line_total VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
