-- Fictional development data, not a held-out benchmark or model result.
PRAGMA foreign_keys = ON;
BEGIN;
CREATE TABLE UNIVERSITY (
    uid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL
);
CREATE TABLE AUTHOR (
    aid INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    uni_id INTEGER NOT NULL REFERENCES UNIVERSITY(uid),
    mentor_aid INTEGER NOT NULL REFERENCES AUTHOR(aid) DEFERRABLE INITIALLY DEFERRED
);
CREATE TABLE PAPER (
    pid INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    publisher_uid INTEGER NOT NULL REFERENCES UNIVERSITY(uid)
);
CREATE TABLE WROTE (
    author_aid INTEGER NOT NULL REFERENCES AUTHOR(aid),
    paper_pid INTEGER NOT NULL REFERENCES PAPER(pid)
);
INSERT INTO UNIVERSITY VALUES (1, 'Waterloo', 'CA'), (2, 'Aarhus', 'DK'),
    (3, 'Fictional US Publisher', 'US');
INSERT INTO AUTHOR VALUES (1, 'Alice', 1, 2), (2, 'Bob', 2, 1), (3, 'Caro', 1, 2);
-- A US publisher with no US-affiliated authors distinguishes a wrong publisher
-- country path from the genuinely empty US-authorship answer.
INSERT INTO PAPER VALUES (10, 'Databases', 2), (11, 'Graphs', 3);
-- Duplicate authorship rows make accidental set comparison visible.
INSERT INTO WROTE VALUES (1, 10), (1, 10), (2, 10), (3, 11), (2, 11);
COMMIT;
