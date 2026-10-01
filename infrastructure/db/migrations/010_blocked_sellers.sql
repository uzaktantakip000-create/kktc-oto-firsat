-- "Kusurlu/sahte" denen ilanın satıcı telefonu: bir daha o satıcıdan 🟢 gelmez (kullanıcının kararı sisteme dönsün)
CREATE TABLE IF NOT EXISTS blocked_sellers (
    phone      TEXT PRIMARY KEY,
    reason     TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
