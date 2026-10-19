CREATE TABLE `article_preferences` (
	`user_id` text NOT NULL,
	`article_id` text NOT NULL,
	`starred` integer DEFAULT false NOT NULL,
	`bad` integer DEFAULT false NOT NULL,
	`starred_updated_at` text,
	`bad_updated_at` text,
	`bad_reason` text,
	`bad_source` text,
	`bad_source_message_id` text,
	PRIMARY KEY(`user_id`, `article_id`)
);
--> statement-breakpoint
CREATE TABLE `reading_state` (
	`user_id` text NOT NULL,
	`article_id` text NOT NULL,
	`read_at` text NOT NULL,
	PRIMARY KEY(`user_id`, `article_id`)
);
