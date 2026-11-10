// Mongo replica-set init and health probe (F-2; PRD §7.2: transactions need a replica set).
// Run by the compose healthcheck as the root user. The root user itself is created by the image
// entrypoint from MONGO_INITDB_ROOT_USERNAME / MONGO_INITDB_ROOT_PASSWORD on an empty volume.
// First run: rs.initiate the single-member set rs0. Every run: exit 0 only on a writable primary.
const REPLICA_SET = "rs0";
const MEMBER_HOST = "mongo:27017";

try {
  rs.status();
} catch (err) {
  if (err.codeName !== "NotYetInitialized") {
    throw err;
  }
  rs.initiate({ _id: REPLICA_SET, members: [{ _id: 0, host: MEMBER_HOST }] });
}

if (!db.hello().isWritablePrimary) {
  quit(1);
}
