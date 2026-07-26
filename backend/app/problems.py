"""LeetCode 风格算法题库，支持按难度随机抽取。

每题：id / title / difficulty / tags / statement(题面, markdown) / reference(参考思路+复杂度, 仅内部)
"""
import random

PROBLEMS = [
    {
        "id": "two-sum",
        "title": "两数之和",
        "difficulty": "简单",
        "tags": ["哈希表", "数组"],
        "statement": (
            "给定一个整数数组 `nums` 和一个目标值 `target`，请在数组中找出和为目标值的两个整数，"
            "返回它们的下标。\n\n"
            "**示例**：`nums = [2,7,11,15], target = 9` → 返回 `[0,1]`。\n\n"
            "假设每种输入只会对应一个答案，且同一个元素不能使用两遍。"
        ),
        "reference": "哈希表存 值->下标，边遍历边查 target-x。时间 O(n)，空间 O(n)。",
    },
    {
        "id": "longest-substring",
        "title": "无重复字符的最长子串",
        "difficulty": "中等",
        "tags": ["滑动窗口", "哈希表"],
        "statement": (
            "给定一个字符串 `s`，请找出其中不含有重复字符的最长子串的长度。\n\n"
            "**示例**：`s = \"abcabcbb\"` → 3（最长子串 `\"abc\"`）。"
        ),
        "reference": "滑动窗口 + 哈希记录字符最近位置，右指针扩张、左指针跳转。时间 O(n)，空间 O(min(n,字符集))。",
    },
    {
        "id": "max-subarray",
        "title": "最大子数组和",
        "difficulty": "中等",
        "tags": ["动态规划", "分治"],
        "statement": (
            "给定整数数组 `nums`，找到一个具有最大和的连续子数组，返回其最大和。\n\n"
            "**示例**：`nums = [-2,1,-3,4,-1,2,1,-5,4]` → 6（子数组 `[4,-1,2,1]`）。"
        ),
        "reference": "Kadane：dp[i]=max(nums[i], dp[i-1]+nums[i])。时间 O(n)，空间 O(1)。也可分治 O(nlogn)。",
    },
    {
        "id": "lru-cache",
        "title": "LRU 缓存",
        "difficulty": "中等",
        "tags": ["哈希表", "双向链表", "设计"],
        "statement": (
            "设计并实现一个 LRU (最近最少使用) 缓存机制。支持 `get(key)` 与 `put(key, value)`，"
            "两者均须 O(1) 时间。当容量满时淘汰最久未使用的项。"
        ),
        "reference": "哈希表 + 双向链表；访问/插入移到头部，淘汰尾部。get/put 均 O(1)。可用 OrderedDict。",
    },
    {
        "id": "reverse-linked-list",
        "title": "反转链表",
        "difficulty": "简单",
        "tags": ["链表", "递归"],
        "statement": "给你单链表的头节点 `head`，请反转链表，并返回反转后的链表头节点。",
        "reference": "迭代三指针 prev/cur/next 逐个翻转。时间 O(n)，空间 O(1)。也可递归。",
    },
    {
        "id": "binary-tree-level-order",
        "title": "二叉树的层序遍历",
        "difficulty": "中等",
        "tags": ["树", "BFS", "队列"],
        "statement": (
            "给你二叉树的根节点 `root`，返回其节点值的层序遍历结果（即逐层地，从左到右访问所有节点）。"
        ),
        "reference": "BFS 用队列，按层出队记录每层大小。时间 O(n)，空间 O(n)。",
    },
    {
        "id": "kth-largest",
        "title": "数组中的第 K 个最大元素",
        "difficulty": "中等",
        "tags": ["堆", "快速选择", "分治"],
        "statement": (
            "给定整数数组 `nums` 和整数 `k`，请返回数组中第 `k` 个最大的元素。\n\n"
            "**示例**：`nums = [3,2,1,5,6,4], k = 2` → 5。要求尽可能优化时间复杂度。"
        ),
        "reference": "小顶堆维护 k 个元素 O(nlogk)；或快速选择 quickselect 平均 O(n)。",
    },
    {
        "id": "coin-change",
        "title": "零钱兑换",
        "difficulty": "中等",
        "tags": ["动态规划"],
        "statement": (
            "给定不同面额的硬币 `coins` 和一个总金额 `amount`，计算凑成总金额所需的最少硬币个数。"
            "如果无法凑成，返回 -1。\n\n**示例**：`coins = [1,2,5], amount = 11` → 3（5+5+1）。"
        ),
        "reference": "完全背包 DP：dp[i]=min(dp[i-c]+1)。时间 O(amount*len(coins))，空间 O(amount)。",
    },
    {
        "id": "num-islands",
        "title": "岛屿数量",
        "difficulty": "中等",
        "tags": ["DFS", "BFS", "并查集"],
        "statement": (
            "给你一个由 `'1'`(陆地) 和 `'0'`(水) 组成的二维网格，请计算网格中岛屿的数量。"
            "岛屿由相邻的陆地水平或垂直连接而成。"
        ),
        "reference": "遍历网格遇到 1 就 DFS/BFS 淹没相连陆地，计数。时间 O(mn)，空间 O(mn)。也可并查集。",
    },
    {
        "id": "merge-intervals",
        "title": "合并区间",
        "difficulty": "中等",
        "tags": ["排序", "数组"],
        "statement": (
            "以数组 `intervals` 表示若干区间，`intervals[i] = [start_i, end_i]`。"
            "请合并所有重叠的区间，返回不重叠的区间数组。\n\n"
            "**示例**：`[[1,3],[2,6],[8,10],[15,18]]` → `[[1,6],[8,10],[15,18]]`。"
        ),
        "reference": "按起点排序后线性扫描合并。时间 O(nlogn)，空间 O(n)。",
    },
    {
        "id": "trap-rain-water",
        "title": "接雨水",
        "difficulty": "困难",
        "tags": ["双指针", "动态规划", "单调栈"],
        "statement": (
            "给定 `n` 个非负整数表示每个宽度为 1 的柱子的高度图，计算按此排列的柱子下雨后能接多少雨水。\n\n"
            "**示例**：`height = [0,1,0,2,1,0,1,3,2,1,2,1]` → 6。"
        ),
        "reference": "双指针 left/right 维护左右最大值，矮的一侧结算。时间 O(n)，空间 O(1)。也可单调栈/DP。",
    },
    {
        "id": "edit-distance",
        "title": "编辑距离",
        "difficulty": "困难",
        "tags": ["动态规划", "字符串"],
        "statement": (
            "给你两个单词 `word1` 和 `word2`，请返回将 `word1` 转换成 `word2` 所使用的最少操作数。"
            "可对一个单词进行插入、删除、替换一个字符。\n\n"
            "**示例**：`word1 = \"horse\", word2 = \"ros\"` → 3。"
        ),
        "reference": "二维 DP：dp[i][j] 由 删/插/替 三者转移。时间 O(mn)，空间 O(mn)，可滚动数组优化到 O(n)。",
    },
]

DIFFICULTY_WEIGHTS = {"简单": 1, "中等": 3, "困难": 1}  # 实习/校招以中等为主


def pick_problem(exclude_ids: list[str] | None = None) -> dict:
    exclude = set(exclude_ids or [])
    pool = [p for p in PROBLEMS if p["id"] not in exclude] or PROBLEMS
    weights = [DIFFICULTY_WEIGHTS.get(p["difficulty"], 1) for p in pool]
    return random.choices(pool, weights=weights, k=1)[0]
