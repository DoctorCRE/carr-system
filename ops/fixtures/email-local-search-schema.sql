-- Supply synthetic decoded hxprobe fields for private index publication tests.
CREATE TABLE messages (
 id INTEGER PRIMARY KEY, sender TEXT, sender_name TEXT, recipients TEXT,
 subject TEXT, body TEXT, message_id TEXT, body_kind TEXT, sent_unix INTEGER, sent_utc TEXT
);
CREATE TABLE message_keys (message_id INTEGER NOT NULL, local_key INTEGER NOT NULL,
 PRIMARY KEY(message_id, local_key));
CREATE TABLE message_objects (local_key INTEGER PRIMARY KEY, account_id INTEGER);
INSERT INTO messages VALUES
 (1,'early@example.invalid','Early','joe@example.invalid','Early history','orchard',
 '<early@example.invalid>','preview',946684800,'2000-01-01 00:00:00'),
 (2,'late@example.invalid','Late','joe@example.invalid','Recent history','orchard full',
 '<late@example.invalid>','full',1767225600,'2026-01-01 00:00:00');
INSERT INTO message_keys VALUES (1,1001),(2,1002);
INSERT INTO message_objects VALUES (1001,42),(1002,42);
