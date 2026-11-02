-- Fictional development data. Physical source names deliberately differ from
-- the logical schema. Each authorship occurrence has its own record key.
-- These IDs distinguish duplicate relationship records; no real entity matching
-- or reference stability under later updates is claimed.
PRAGMA foreign_keys = ON;
BEGIN;
CREATE TABLE uni_directory(
    institution_key INTEGER PRIMARY KEY,
    institution_label TEXT NOT NULL,
    nation_code TEXT NOT NULL
);
CREATE TABLE researcher_rows(
    person_key INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    institution_key INTEGER NOT NULL REFERENCES uni_directory(institution_key),
    mentor_key INTEGER NOT NULL REFERENCES researcher_rows(person_key)
        DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE publication_rows(
    publication_key INTEGER PRIMARY KEY,
    heading TEXT NOT NULL,
    publisher_key INTEGER NOT NULL REFERENCES uni_directory(institution_key)
);
CREATE TABLE authorship_events(
    event_key INTEGER PRIMARY KEY,
    person_key INTEGER NOT NULL REFERENCES researcher_rows(person_key),
    publication_key INTEGER NOT NULL REFERENCES publication_rows(publication_key)
);
INSERT INTO uni_directory VALUES
    (1,'Waterloo','CA'), (2,'Aarhus','DK'), (3,'Fictional US Publisher','US');
INSERT INTO researcher_rows VALUES
    (1,'Alice',1,2), (2,'Bob',2,1), (3,'Caro',1,2);
INSERT INTO publication_rows VALUES (10,'Databases',2), (11,'Graphs',3);
INSERT INTO authorship_events VALUES
    (101,1,10), (102,1,10), (103,2,10), (104,3,11), (105,2,11);
COMMIT;
