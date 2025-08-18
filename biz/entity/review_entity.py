class MergeRequestReviewEntity:
    def __init__(self, project_name: str, author: str, source_branch: str, target_branch: str, updated_at: int,
                 commits: list, score: float, url: str, review_result: str, url_slug: str, webhook_data: dict,
                 additions: int, deletions: int, last_commit_id: str):
        self.project_name = project_name      # 项目名称
        self.author = author                  # 提交作者
        self.source_branch = source_branch    # 源分支
        self.target_branch = target_branch    # 目标分支
        self.updated_at = updated_at          # 更新时间（时间戳）
        self.commits = commits                # 提交列表
        self.score = score                    # 评审分数
        self.url = url                        # 评审链接
        self.review_result = review_result    # 评审结果
        self.url_slug = url_slug              # URL标识
        self.webhook_data = webhook_data      # webhook数据
        self.additions = additions            # 新增行数
        self.deletions = deletions            # 删除行数
        self.last_commit_id = last_commit_id  # 最后一次提交的ID

    @property
    def commit_messages(self):
        # 合并所有 commit 的 message 属性，用分号分隔
        return "; ".join(commit["message"].strip() for commit in self.commits)


class PushReviewEntity:
    def __init__(self, project_name: str, author: str, branch: str, updated_at: int, commits: list, score: float,
                 review_result: str, url_slug: str, webhook_data: dict, additions: int, deletions: int):
        self.project_name = project_name      # 项目名称
        self.author = author                  # 提交作者
        self.branch = branch                  # 提交分支
        self.updated_at = updated_at          # 更新时间（时间戳）
        self.commits = commits                # 提交列表
        self.score = score                    # 评审分数
        self.review_result = review_result    # 评审结果
        self.url_slug = url_slug              # URL标识
        self.webhook_data = webhook_data      # webhook数据
        self.additions = additions            # 新增行数
        self.deletions = deletions            # 删除行数

    @property
    def commit_messages(self):
        # 合并所有 commit 的 message 属性，用分号分隔
        return "; ".join(commit["message"].strip() for commit in self.commits)

