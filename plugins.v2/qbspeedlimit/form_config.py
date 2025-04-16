"""
QB智能限速插件表单配置
"""

def get_rules_table():
    """
    获取规则表格配置
    """
    return {
        "component": "VRow",
        "content": [
            {
                "component": "VCol",
                "props": {"cols": 12},
                "content": [
                    {
                        "component": "VDataTable",
                        "props": {
                            "headers": [
                                {"title": "规则名称", "key": "name", "sortable": True},
                                {"title": "Tracker匹配", "key": "tracker_pattern"},
                                {"title": "上传限速", "key": "upload_limit"},
                                {"title": "下载限速", "key": "download_limit"},
                                {"title": "启用", "key": "enabled"},
                                {"title": "操作", "key": "actions", "sortable": False}
                            ],
                            "item-key": "rule_id",
                            "show-select": False,
                            "disable-pagination": True,
                            "hide-default-footer": True
                        },
                        "slots": {
                            "item.actions": {
                                "component": "VBtn",
                                "props": {
                                    "x-small": True,
                                    "text": True,
                                    "@click": "editRule(item)"
                                },
                                "content": "编辑"
                            },
                            "item.enabled": {
                                "component": "VSwitch",
                                "props": {
                                    "model": "item.enabled",
                                    "small": True
                                }
                            }
                        }
                    }
                ]
            }
        ]
    }

def get_form_config(rules):
    """
    获取表单配置
    :param rules: 规则列表
    :return: 表单配置
    """
    rules_table = get_rules_table()
    # 添加规则数据
    if "content" in rules_table and len(rules_table["content"]) > 0:
        table_component = rules_table["content"][0]["content"][0]
        if "props" in table_component:
            table_component["props"]["items"] = rules

    return [
        {
            "component": "VForm",
            "content": [
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "enabled",
                                        "label": "启用插件",
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "onlyonce",
                                        "label": "立即运行一次"
                                    },
                                }
                            ],
                        },
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                {
                                    "component": "VSwitch",
                                    "props": {
                                        "model": "notify",
                                        "label": "发送通知",
                                    },
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "md": 6},
                            "content": [
                                { 
                                    "component": "VTextField",
                                    "props": {
                                        "model": "cron",
                                        "label": "监测周期",
                                        "placeholder": "5位cron表达式，默认每5分钟"
                                    }
                                }
                            ],
                        },
                    ],
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [
                                {
                                    "component": "VAlert",
                                    "props": {
                                        "type": "info",
                                        "variant": "tonal",
                                        "text": "本插件会根据配置的规则定时遍历所有QB种子，若tracker匹配规则则自动限速。",
                                    },
                                }
                            ],
                        }
                    ],
                },
                {
                    "component": "VDivider",
                    "props": {"class": "my-4"}
                },
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12},
                            "content": [
                                {
                                    "component": "VSubheader",
                                    "props": {"class": "text-h6"},
                                    "content": "限速规则配置"
                                }
                            ]
                        }
                    ]
                },
                # 添加规则表格
                rules_table,
                {
                    "component": "VRow",
                    "content": [
                        {
                            "component": "VCol",
                            "props": {"cols": 12, "class": "d-flex justify-end"},
                            "content": [
                                {
                                    "component": "VBtn",
                                    "props": {
                                        "color": "primary",
                                        "@click": "openAddRuleDialog"
                                    },
                                    "content": "添加规则"
                                }
                            ]
                        }
                    ]
                }
            ],
        }
    ]