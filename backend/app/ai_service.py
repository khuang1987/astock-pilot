from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _schema() -> dict:
    return {
        "name": "astock_ai_review",
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                "summary": {"type": "string"},
                "market_view": {"type": "string"},
                "candidate_view": {"type": "string"},
                "position_view": {"type": "string"},
                "action_suggestion": {"type": "string"},
                "plan_reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "plan_id": {"type": "string"},
                            "symbol": {"type": "string"},
                            "verdict": {"type": "string", "enum": ["approve", "caution", "reject"]},
                            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                            "reason": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["plan_id", "symbol", "verdict", "risk_level", "reason", "suggestion"],
                    },
                },
                "stock_reviews": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "symbol": {"type": "string"},
                            "decision": {"type": "string"},
                            "risk_level": {"type": "string", "enum": ["low", "medium", "high"]},
                            "summary": {"type": "string"},
                            "suggestion": {"type": "string"},
                        },
                        "required": ["symbol", "decision", "risk_level", "summary", "suggestion"],
                    },
                },
            },
            "required": [
                "risk_level",
                "summary",
                "market_view",
                "candidate_view",
                "position_view",
                "action_suggestion",
                "plan_reviews",
                "stock_reviews",
            ],
        },
        "strict": True,
    }


def _prompt() -> str:
    return (
        "你是A股短线模拟交易系统的最终风险审核员，只审核模拟计划，不提供实盘投资建议，不承诺收益。"
        "必须只基于输入JSON中的数据，不得新增不存在的股票、价格、新闻或外部事实。"
        "输入中的 message_context 是公告、新闻、研报等消息面证据；你需要结合技术评分、持仓、交易计划和消息面，"
        "给出是否按技术分析计划执行的最终审核结论。"
        "规则策略是主决策层，你是最终审核层：没有明确冲突时可 approve；发现消息面或风险面不确定但未达到否决程度时 caution；"
        "发现重大利空、公告风险、数据矛盾或计划与规则冲突时 reject。"
        "verdict 表示最终执行方向，risk_level 表示风险强度；approve+medium 属于谨慎放行，caution 属于条件放行，最终仍必须满足价格、额度和风控规则。"
        "审核对象必须覆盖：所有 pending trade_plans、持仓股、评分候选股和 stock_evaluations 中出现的股票。"
        "审核顺序固定为：1市场风险，2消息面风险，3持仓风险，4候选质量，5交易计划触发条件，6仓位和交易限制。"
        "风险等级只能输出 low、medium、high；计划结论只能输出 approve、caution、reject。"
        "同一输入下应尽量给出一致结论；不要因为措辞偏好改变结论。"
        "必须输出JSON，不要输出JSON以外内容。"
    )


def review_prompt() -> str:
    return _prompt()


def _extract_json_text(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def call_openai_review(api_key: str, model: str, payload: dict, base_url: str = "https://api.openai.com/v1") -> dict:
    if not api_key:
        raise RuntimeError("OpenAI API key 未配置")
    body = {
        "model": model or "gpt-4o-mini",
        "input": [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        "text": {
            "format": {
                "type": "json_schema",
                "json_schema": _schema(),
            }
        },
    }
    base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
    request = Request(
        f"{base_url}/responses",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI 调用失败 HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"OpenAI 连接失败: {exc.reason}") from exc

    text = raw.get("output_text")
    if not text:
        chunks: list[str] = []
        for item in raw.get("output", []):
            for content in item.get("content", []):
                if content.get("type") in ("output_text", "text"):
                    chunks.append(content.get("text", ""))
        text = "".join(chunks)
    if not text:
        raise RuntimeError("OpenAI 返回为空")
    return json.loads(text)


def call_chat_compatible_review(api_key: str, model: str, payload: dict, base_url: str, provider_name: str) -> dict:
    if not api_key:
        raise RuntimeError(f"{provider_name} API key 未配置")
    user_content = (
        "请严格输出 JSON，字段必须包含：risk_level, summary, market_view, candidate_view, "
        "position_view, action_suggestion, plan_reviews, stock_reviews。"
        "plan_reviews 每项包含 plan_id, symbol, verdict, risk_level, reason, suggestion，verdict 表示是否按技术计划执行。"
        "stock_reviews 每项包含 symbol, decision, risk_level, summary, suggestion，decision 应给出执行/谨慎/暂停/持有/卖出观察等结论。\n"
        + json.dumps(payload, ensure_ascii=False)
    )
    body = {
        "model": model or "deepseek-chat",
        "messages": [
            {"role": "system", "content": _prompt()},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }
    base_url = (base_url or "https://api.deepseek.com").rstrip("/")
    request = Request(
        f"{base_url}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=90) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"{provider_name} 调用失败 HTTP {exc.code}: {detail[:300]}") from exc
    except URLError as exc:
        raise RuntimeError(f"{provider_name} 连接失败: {exc.reason}") from exc
    content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
    if not content:
        raise RuntimeError(f"{provider_name} 返回为空")
    return _extract_json_text(content)


def call_ai_review(provider: str, api_key: str, model: str, base_url: str, payload: dict) -> dict:
    if provider == "deepseek":
        return call_chat_compatible_review(api_key, model or "deepseek-chat", payload, base_url or "https://api.deepseek.com", "DeepSeek")
    return call_openai_review(api_key, model or "gpt-4o-mini", payload, base_url or "https://api.openai.com/v1")
