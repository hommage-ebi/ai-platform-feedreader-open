import { sqliteTable, text, integer, primaryKey } from 'drizzle-orm/sqlite-core';
export const readingState = sqliteTable('reading_state', {
  userId: text('user_id').notNull(),
  articleId: text('article_id').notNull(),
  readAt: text('read_at').notNull(),
}, table => [primaryKey({ columns: [table.userId, table.articleId] })]);
export const articlePreferences = sqliteTable('article_preferences', {
  userId: text('user_id').notNull(),
  articleId: text('article_id').notNull(),
  starred: integer('starred', {mode:'boolean'}).notNull().default(false),
  bad: integer('bad', {mode:'boolean'}).notNull().default(false),
  starredUpdatedAt: text('starred_updated_at'),
  badUpdatedAt: text('bad_updated_at'),
  badReason: text('bad_reason'),
  badSource: text('bad_source'),
  badSourceMessageId: text('bad_source_message_id'),
}, table => [primaryKey({ columns: [table.userId, table.articleId] })]);
