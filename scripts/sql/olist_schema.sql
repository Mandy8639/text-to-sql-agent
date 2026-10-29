-- Build the typed, cleaned `olist` schema from the all-TEXT `staging` schema.
-- Runs inside one transaction (see scripts/load_data.py): either the whole load succeeds or the
-- previous `olist` schema is left untouched.
--
-- Column names follow the original Kaggle dataset (widely documented, and familiar to LLMs),
-- except for fixing the `lenght` typos in products.

CREATE EXTENSION IF NOT EXISTS unaccent;

DROP SCHEMA IF EXISTS olist CASCADE;
CREATE SCHEMA olist;

-- ---------------------------------------------------------------------------------------------
-- category_translation: Portuguese -> English category names.
-- Two categories used by products are missing from the source file; we add them here.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.category_translation (
    product_category_name         TEXT PRIMARY KEY,
    product_category_name_english TEXT NOT NULL
);

INSERT INTO olist.category_translation
SELECT product_category_name, product_category_name_english
FROM staging.category_translation;

INSERT INTO olist.category_translation VALUES
    ('pc_gamer', 'pc_gamer'),
    ('portateis_cozinha_e_preparadores_de_alimentos', 'portable_kitchen_and_food_preparers');

-- ---------------------------------------------------------------------------------------------
-- geolocation: the raw file has ~1M rows but only ~19k zip prefixes (many points per prefix,
-- 26% exact duplicates, accent variants like "são paulo"/"sao paulo", a few points outside
-- Brazil). Joining customers to the raw table multiplies rows, a classic analytics bug.
-- We collapse it to ONE row per zip prefix: mean coordinates of points inside Brazil's bounding
-- box, and the most common (accent-stripped) city and state.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.geolocation (
    geolocation_zip_code_prefix CHAR(5) PRIMARY KEY,
    geolocation_lat             DOUBLE PRECISION,
    geolocation_lng             DOUBLE PRECISION,
    geolocation_city            TEXT NOT NULL,
    geolocation_state           CHAR(2) NOT NULL
);

INSERT INTO olist.geolocation
SELECT
    geolocation_zip_code_prefix,
    avg(geolocation_lat::float8) FILTER (WHERE in_brazil),
    avg(geolocation_lng::float8) FILTER (WHERE in_brazil),
    mode() WITHIN GROUP (ORDER BY lower(public.unaccent(geolocation_city))),
    mode() WITHIN GROUP (ORDER BY upper(geolocation_state))
FROM (
    SELECT *,
           geolocation_lat::float8 BETWEEN -34 AND 5.5
           AND geolocation_lng::float8 BETWEEN -74 AND -34 AS in_brazil
    FROM staging.geolocation
) g
GROUP BY geolocation_zip_code_prefix;

-- ---------------------------------------------------------------------------------------------
-- customers: one row per ORDER-level customer id. The same person can have several
-- customer_ids; customer_unique_id identifies the person.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.customers (
    customer_id              TEXT PRIMARY KEY,
    customer_unique_id       TEXT NOT NULL,
    customer_zip_code_prefix CHAR(5) NOT NULL,
    customer_city            TEXT NOT NULL,
    customer_state           CHAR(2) NOT NULL
);

INSERT INTO olist.customers
SELECT customer_id, customer_unique_id, customer_zip_code_prefix,
       customer_city, upper(customer_state)
FROM staging.customers;

-- ---------------------------------------------------------------------------------------------
-- sellers
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.sellers (
    seller_id              TEXT PRIMARY KEY,
    seller_zip_code_prefix CHAR(5) NOT NULL,
    seller_city            TEXT NOT NULL,
    seller_state           CHAR(2) NOT NULL
);

INSERT INTO olist.sellers
SELECT seller_id, seller_zip_code_prefix, seller_city, upper(seller_state)
FROM staging.sellers;

-- ---------------------------------------------------------------------------------------------
-- products: English category name denormalized onto the table so the agent rarely needs the
-- translation join. Integer columns arrive as text like "40.0"; `lenght` typos are fixed.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.products (
    product_id                    TEXT PRIMARY KEY,
    product_category_name         TEXT REFERENCES olist.category_translation,
    product_category_name_english TEXT,
    product_name_length           INTEGER,
    product_description_length    INTEGER,
    product_photos_qty            INTEGER,
    product_weight_g              INTEGER,
    product_length_cm             INTEGER,
    product_height_cm             INTEGER,
    product_width_cm              INTEGER
);

INSERT INTO olist.products
SELECT p.product_id,
       p.product_category_name,
       t.product_category_name_english,
       p.product_name_lenght::numeric::int,
       p.product_description_lenght::numeric::int,
       p.product_photos_qty::numeric::int,
       p.product_weight_g::numeric::int,
       p.product_length_cm::numeric::int,
       p.product_height_cm::numeric::int,
       p.product_width_cm::numeric::int
FROM staging.products p
LEFT JOIN olist.category_translation t USING (product_category_name);

-- ---------------------------------------------------------------------------------------------
-- orders
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.orders (
    order_id                      TEXT PRIMARY KEY,
    customer_id                   TEXT NOT NULL UNIQUE REFERENCES olist.customers,
    order_status                  TEXT NOT NULL CHECK (order_status IN (
        'created', 'approved', 'invoiced', 'processing', 'shipped',
        'delivered', 'canceled', 'unavailable')),
    order_purchase_timestamp      TIMESTAMP NOT NULL,
    order_approved_at             TIMESTAMP,
    order_delivered_carrier_date  TIMESTAMP,
    order_delivered_customer_date TIMESTAMP,
    order_estimated_delivery_date TIMESTAMP NOT NULL
);

INSERT INTO olist.orders
SELECT order_id, customer_id, order_status,
       order_purchase_timestamp::timestamp,
       order_approved_at::timestamp,
       order_delivered_carrier_date::timestamp,
       order_delivered_customer_date::timestamp,
       order_estimated_delivery_date::timestamp
FROM staging.orders;

-- ---------------------------------------------------------------------------------------------
-- order_items: one row per UNIT sold (quantity = number of rows; order_item_id is 1..n).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.order_items (
    order_id            TEXT NOT NULL REFERENCES olist.orders,
    order_item_id       INTEGER NOT NULL,
    product_id          TEXT NOT NULL REFERENCES olist.products,
    seller_id           TEXT NOT NULL REFERENCES olist.sellers,
    shipping_limit_date TIMESTAMP NOT NULL,
    price               NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    freight_value       NUMERIC(10, 2) NOT NULL CHECK (freight_value >= 0),
    PRIMARY KEY (order_id, order_item_id)
);

INSERT INTO olist.order_items
SELECT order_id, order_item_id::int, product_id, seller_id,
       shipping_limit_date::timestamp, price::numeric, freight_value::numeric
FROM staging.order_items;

-- ---------------------------------------------------------------------------------------------
-- order_payments: an order can be paid with several methods (payment_sequential 1..n).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.order_payments (
    order_id             TEXT NOT NULL REFERENCES olist.orders,
    payment_sequential   INTEGER NOT NULL,
    payment_type         TEXT NOT NULL CHECK (payment_type IN (
        'credit_card', 'boleto', 'voucher', 'debit_card', 'not_defined')),
    payment_installments INTEGER NOT NULL CHECK (payment_installments >= 0),
    payment_value        NUMERIC(10, 2) NOT NULL CHECK (payment_value >= 0),
    PRIMARY KEY (order_id, payment_sequential)
);

INSERT INTO olist.order_payments
SELECT order_id, payment_sequential::int, payment_type,
       payment_installments::int, payment_value::numeric
FROM staging.order_payments;

-- ---------------------------------------------------------------------------------------------
-- order_reviews: review_id is NOT unique on its own (one review can cover several orders) and
-- an order can have several reviews, so the key is (review_id, order_id).
-- ---------------------------------------------------------------------------------------------
CREATE TABLE olist.order_reviews (
    review_id               TEXT NOT NULL,
    order_id                TEXT NOT NULL REFERENCES olist.orders,
    review_score            SMALLINT NOT NULL CHECK (review_score BETWEEN 1 AND 5),
    review_comment_title    TEXT,
    review_comment_message  TEXT,
    review_creation_date    TIMESTAMP NOT NULL,
    review_answer_timestamp TIMESTAMP NOT NULL,
    PRIMARY KEY (review_id, order_id)
);

INSERT INTO olist.order_reviews
SELECT review_id, order_id, review_score::smallint,
       review_comment_title, review_comment_message,
       review_creation_date::timestamp, review_answer_timestamp::timestamp
FROM staging.order_reviews;

-- ---------------------------------------------------------------------------------------------
-- Indexes for the joins and filters analytics questions use most. Primary keys are already
-- indexed; Postgres does not index foreign-key columns automatically.
-- ---------------------------------------------------------------------------------------------
CREATE INDEX ON olist.customers (customer_unique_id);
CREATE INDEX ON olist.customers (customer_state);
CREATE INDEX ON olist.customers (customer_zip_code_prefix);
CREATE INDEX ON olist.sellers (seller_state);
CREATE INDEX ON olist.products (product_category_name_english);
CREATE INDEX ON olist.orders (order_purchase_timestamp);
CREATE INDEX ON olist.orders (order_status);
CREATE INDEX ON olist.order_items (product_id);
CREATE INDEX ON olist.order_items (seller_id);
CREATE INDEX ON olist.order_payments (payment_type);
CREATE INDEX ON olist.order_reviews (order_id);

-- ---------------------------------------------------------------------------------------------
-- Table comments: a short description lives in the database too, so anyone inspecting the
-- schema with psql (\dt+) sees it. The full documentation is docs/schema.yaml.
-- ---------------------------------------------------------------------------------------------
COMMENT ON TABLE olist.orders IS 'One row per order. Use order_purchase_timestamp for time analysis.';
COMMENT ON TABLE olist.order_items IS 'One row per unit sold. price excludes freight.';
COMMENT ON TABLE olist.order_payments IS 'One row per payment method used on an order.';
COMMENT ON TABLE olist.order_reviews IS 'Customer satisfaction reviews (1-5).';
COMMENT ON TABLE olist.customers IS 'One row per order-level customer id; customer_unique_id is the person.';
COMMENT ON TABLE olist.products IS 'Product catalog with English category name.';
COMMENT ON TABLE olist.sellers IS 'Marketplace sellers.';
COMMENT ON TABLE olist.geolocation IS 'One row per 5-digit zip prefix (deduplicated).';
COMMENT ON TABLE olist.category_translation IS 'Portuguese to English product category names.';
