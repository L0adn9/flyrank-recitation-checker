-- Attempts checked live, over a WebSocket while the person recited, rather than uploaded as a file.
ALTER TABLE attempts ADD COLUMN live BOOLEAN NOT NULL DEFAULT false;
