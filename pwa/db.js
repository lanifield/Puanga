// Minimal IndexedDB wrapper for the offline entry queue and settings.
// No external dependency — the whole point of the offline path is that it
// works with nothing but what the browser already gives an installed PWA.

const DB_NAME = "puanga";
const DB_VERSION = 1;
const STORE_QUEUE = "queue";
const STORE_SETTINGS = "settings";

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE_QUEUE)) {
        db.createObjectStore(STORE_QUEUE, { keyPath: "id" });
      }
      if (!db.objectStoreNames.contains(STORE_SETTINGS)) {
        db.createObjectStore(STORE_SETTINGS, { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

function tx(db, store, mode) {
  return db.transaction(store, mode).objectStore(store);
}

export async function queueEntry(entry) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = tx(db, STORE_QUEUE, "readwrite").put(entry);
    req.onsuccess = () => resolve(entry);
    req.onerror = () => reject(req.error);
  });
}

export async function getQueuedEntries() {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = tx(db, STORE_QUEUE, "readonly").getAll();
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function removeQueuedEntries(ids) {
  const db = await openDb();
  const store = tx(db, STORE_QUEUE, "readwrite");
  await Promise.all(
    ids.map(
      (id) =>
        new Promise((resolve, reject) => {
          const req = store.delete(id);
          req.onsuccess = () => resolve();
          req.onerror = () => reject(req.error);
        })
    )
  );
}

export async function getSetting(key, fallback) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = tx(db, STORE_SETTINGS, "readonly").get(key);
    req.onsuccess = () => resolve(req.result ? req.result.value : fallback);
    req.onerror = () => reject(req.error);
  });
}

export async function setSetting(key, value) {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const req = tx(db, STORE_SETTINGS, "readwrite").put({ key, value });
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
  });
}
