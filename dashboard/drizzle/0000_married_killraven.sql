CREATE TABLE `chunks` (
	`id` text PRIMARY KEY NOT NULL,
	`experiment` text NOT NULL,
	`seq` integer NOT NULL,
	`attempt` text NOT NULL,
	`channel` text NOT NULL,
	`stream` text NOT NULL,
	`source_at` text NOT NULL,
	`hash` text NOT NULL,
	`bytes` integer NOT NULL,
	`metadata` text NOT NULL
);
--> statement-breakpoint
CREATE UNIQUE INDEX `chunks_experiment_seq` ON `chunks` (`experiment`,`seq`);--> statement-breakpoint
CREATE INDEX `chunks_stream` ON `chunks` (`experiment`,`stream`,`seq`);--> statement-breakpoint
CREATE INDEX `chunks_attempt_channel` ON `chunks` (`experiment`,`attempt`,`channel`,`seq`);--> statement-breakpoint
CREATE TABLE `experiments` (
	`id` text PRIMARY KEY NOT NULL,
	`version` integer NOT NULL,
	`payload` text NOT NULL,
	`sha256` text NOT NULL,
	`updated_at` text NOT NULL
);
