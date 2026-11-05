use rusqlite::{params, Transaction};

const SCHEMA: &str = "outlook-contact-observation/v1";
const SOURCE_DIGEST: &str = include_str!(concat!(env!("OUT_DIR"), "/contact_source_digest"));

pub fn export(tx: &Transaction<'_>, container_version: u8) -> rusqlite::Result<()> {
    tx.execute_batch(
        "CREATE TABLE contact_capability (
             schema TEXT NOT NULL,
             source_digest TEXT NOT NULL CHECK(length(source_digest) = 64),
             container_version INTEGER NOT NULL CHECK(container_version BETWEEN 0 AND 255),
             outlook_build TEXT CHECK(outlook_build IS NULL),
             layout_id TEXT CHECK(layout_id IS NULL),
             capability TEXT NOT NULL CHECK(capability = 'unsupported'),
             reason TEXT NOT NULL CHECK(reason = 'contact_layout_unverified'),
             singleton INTEGER PRIMARY KEY CHECK(singleton = 1)
         );
         CREATE TABLE contact_observations (
             observation TEXT NOT NULL CHECK(length(CAST(observation AS BLOB)) <= 65536)
                 CHECK(json_valid(observation))
         );
         CREATE TRIGGER contact_layout_gate BEFORE INSERT ON contact_observations
         BEGIN
             SELECT RAISE(ABORT, 'contact_layout_unverified');
         END;",
    )?;
    tx.execute(
        "INSERT INTO contact_capability
         (schema, source_digest, container_version, outlook_build, layout_id, capability, reason, singleton)
         VALUES (?1, ?2, ?3, NULL, NULL, 'unsupported', 'contact_layout_unverified', 1)",
        params![SCHEMA, SOURCE_DIGEST, container_version],
    )?;
    Ok(())
}
