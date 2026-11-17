-- 故障事件归组：作业间关联、归组版本（草稿/生效/归档），挖掘批次冻结归组上下文，
-- 备件包挂载联用证据并支持暂停。MySQL 8.0 / utf8mb4。

-- 作业间关联（规范化为 left<right，天然去重；撤销保留审计痕迹）
DROP TABLE IF EXISTS `job_links`;
CREATE TABLE `job_links` (
  `id` int NOT NULL AUTO_INCREMENT,
  `left_job_id` int NOT NULL,
  `right_job_id` int NOT NULL,
  `status` varchar(20) DEFAULT 'active',
  `created` varchar(40) DEFAULT NULL,
  `revoked` varchar(40) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_job_pair` (`left_job_id`,`right_job_id`),
  KEY `ix_job_links_left` (`left_job_id`),
  KEY `ix_job_links_right` (`right_job_id`),
  CONSTRAINT `job_links_ibfk_1` FOREIGN KEY (`left_job_id`) REFERENCES `jobs` (`id`),
  CONSTRAINT `job_links_ibfk_2` FOREIGN KEY (`right_job_id`) REFERENCES `jobs` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 归组版本：草稿随关联变动重算（同一草稿行覆盖，不新增版本），发布后不可变
DROP TABLE IF EXISTS `group_versions`;
CREATE TABLE `group_versions` (
  `id` int NOT NULL AUTO_INCREMENT,
  `status` varchar(20) DEFAULT 'draft',
  `groups` json DEFAULT NULL,
  `link_ids` json DEFAULT NULL,
  `created` varchar(40) DEFAULT NULL,
  `published` varchar(40) DEFAULT NULL,
  `note` text,
  PRIMARY KEY (`id`),
  KEY `ix_group_versions_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- 挖掘批次记录所依据的生效归组版本与冻结的组事务数
ALTER TABLE `mining_runs`
  ADD COLUMN `group_version_id` int DEFAULT NULL AFTER `error`,
  ADD COLUMN `grouping` json DEFAULT NULL AFTER `group_version_id`;

-- 备件包增加联用证据快照与暂停状态
ALTER TABLE `kits`
  ADD COLUMN `evidence` json DEFAULT NULL AFTER `status`,
  MODIFY COLUMN `status` varchar(20) DEFAULT 'draft';
