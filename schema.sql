PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, email TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','gerente','vendedor','logistica','estoque','financeiro')),
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS clients (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, phone TEXT, phone_normalized TEXT,
 email TEXT, document TEXT, city TEXT, state TEXT, address TEXT, vehicle_model TEXT, vehicle_year INTEGER,
 modifications TEXT, interests TEXT, notes TEXT, tags TEXT, origin TEXT, owner_id INTEGER REFERENCES users(id),
 last_contact_at TEXT, archived INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(phone_normalized);
CREATE INDEX IF NOT EXISTS idx_clients_owner ON clients(owner_id);
CREATE TABLE IF NOT EXISTS activities (
 id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id),
 kind TEXT NOT NULL, description TEXT NOT NULL, user_id INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS opportunities (
 id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id), product TEXT NOT NULL,
 value_cents INTEGER NOT NULL DEFAULT 0 CHECK(value_cents >= 0), probability INTEGER NOT NULL DEFAULT 30 CHECK(probability BETWEEN 0 AND 100),
 stage TEXT NOT NULL DEFAULT 'Novo contato', expected_close TEXT, source TEXT, owner_id INTEGER REFERENCES users(id),
 notes TEXT, loss_reason TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_opportunities_stage ON opportunities(stage);
CREATE TABLE IF NOT EXISTS followups (
 id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id),
 opportunity_id INTEGER REFERENCES opportunities(id), product TEXT NOT NULL, quoted_value_cents INTEGER NOT NULL DEFAULT 0,
 objection TEXT, due_at TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'Média', owner_id INTEGER REFERENCES users(id),
 status TEXT NOT NULL DEFAULT 'Pendente', notes TEXT, category TEXT, recurring TEXT, last_contact_at TEXT,
 completed_at TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_followups_due ON followups(due_at, status);
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER NOT NULL REFERENCES clients(id),
 source TEXT NOT NULL DEFAULT 'Manual', external_id TEXT UNIQUE, order_number TEXT NOT NULL UNIQUE,
 total_cents INTEGER NOT NULL DEFAULT 0, discount_cents INTEGER NOT NULL DEFAULT 0, shipping_cents INTEGER NOT NULL DEFAULT 0,
 payment_status TEXT NOT NULL DEFAULT 'Pendente', operational_status TEXT NOT NULL DEFAULT 'Pedido recebido',
 shipping_status TEXT NOT NULL DEFAULT 'Não enviado', carrier TEXT, tracking_code TEXT, destination_address TEXT,
 due_at TEXT, owner_id INTEGER REFERENCES users(id), notes TEXT,
 packed INTEGER NOT NULL DEFAULT 0, packed_at TEXT, customer_notified INTEGER NOT NULL DEFAULT 0, shipped_at TEXT, delivered_at TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(operational_status);
CREATE TABLE IF NOT EXISTS order_items (
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
 product TEXT NOT NULL, sku TEXT, quantity INTEGER NOT NULL DEFAULT 1 CHECK(quantity>0), unit_cents INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
 id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER REFERENCES clients(id), order_id INTEGER REFERENCES orders(id),
 title TEXT NOT NULL, description TEXT, due_at TEXT, priority TEXT NOT NULL DEFAULT 'Média',
 assignee_id INTEGER REFERENCES users(id), status TEXT NOT NULL DEFAULT 'Pendente', recurrence TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS audit_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER REFERENCES users(id),
 entity TEXT NOT NULL, entity_id INTEGER NOT NULL, action TEXT NOT NULL, before_json TEXT, after_json TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS integrations (
 service TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'Desconectado',
 last_sync_at TEXT, last_error TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO integrations (service) VALUES ('Nuvemshop'), ('WhatsApp Business'), ('Melhor Envio');

CREATE TABLE IF NOT EXISTS app_settings (
 key TEXT PRIMARY KEY, value TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE, sku TEXT,
 active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO products (name) VALUES ('Coletor Cat Delete');
CREATE TABLE IF NOT EXISTS production_batches (
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE, product_id INTEGER NOT NULL REFERENCES products(id),
 qty_planned INTEGER NOT NULL CHECK(qty_planned>0), qty_finished INTEGER NOT NULL DEFAULT 0 CHECK(qty_finished>=0),
 status TEXT NOT NULL DEFAULT 'Planejado' CHECK(status IN ('Planejado','Em produção','Finalizado','Cancelado')),
 due_at TEXT, started_at TEXT, finished_at TEXT, owner_id INTEGER REFERENCES users(id), notes TEXT,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_batches_status ON production_batches(status);
CREATE TABLE IF NOT EXISTS production_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT, batch_id INTEGER NOT NULL REFERENCES production_batches(id),
 qty INTEGER NOT NULL, note TEXT, user_id INTEGER REFERENCES users(id),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
