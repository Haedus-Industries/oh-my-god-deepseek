import { integer, sqliteTable, text, uniqueIndex, index } from "drizzle-orm/sqlite-core";

export const experiments = sqliteTable("experiments", {
  id: text("id").primaryKey(),
  version: integer("version").notNull(),
  payload: text("payload").notNull(),
  sha256: text("sha256").notNull(),
  updatedAt: text("updated_at").notNull(),
});

export const chunks = sqliteTable("chunks", {
  id: text("id").primaryKey(),
  experiment: text("experiment").notNull(),
  seq: integer("seq").notNull(),
  attempt: text("attempt").notNull(),
  channel: text("channel").notNull(),
  stream: text("stream").notNull(),
  sourceAt: text("source_at").notNull(),
  hash: text("hash").notNull(),
  bytes: integer("bytes").notNull(),
  metadata: text("metadata").notNull(),
}, (table) => [
  uniqueIndex("chunks_experiment_seq").on(table.experiment, table.seq),
  index("chunks_stream").on(table.experiment, table.stream, table.seq),
  index("chunks_attempt_channel").on(table.experiment, table.attempt, table.channel, table.seq),
]);
