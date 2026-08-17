(() => {
  "use strict";

  const DEFAULT_TIMEOUTS = {
    health: 8000,
    query: 75000,
  };

  class RagApiError extends Error {
    constructor(message, options = {}) {
      super(message);
      this.name = "RagApiError";
      this.kind = options.kind || "unknown";
      this.status = options.status || 0;
      this.detail = options.detail || "";
      this.payload = options.payload;
    }
  }

  function isRecord(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value);
  }

  function isString(value) {
    return typeof value === "string";
  }

  function isFiniteNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  function getApiBase() {
    const params = new URLSearchParams(window.location.search);
    const configured = window.__RAG_API_BASE__ || params.get("api");
    if (!configured) return window.location.origin;
    try {
      return new URL(configured, window.location.href).origin;
    } catch {
      return window.location.origin;
    }
  }

  function buildUrl(path) {
    const base = getApiBase().replace(/\/$/, "");
    return `${base}${path}`;
  }

  function parseErrorDetail(payload) {
    if (!isRecord(payload)) return "";
    if (isString(payload.detail)) return payload.detail;
    if (isString(payload.message)) return payload.message;
    return "";
  }

  async function requestJson(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(
      () => controller.abort(),
      options.timeoutMs || DEFAULT_TIMEOUTS.query,
    );

    try {
      const response = await fetch(buildUrl(path), {
        method: options.method || "GET",
        headers: {
          Accept: "application/json",
          ...(options.body ? { "Content-Type": "application/json" } : {}),
          ...(options.headers || {}),
        },
        body: options.body ? JSON.stringify(options.body) : undefined,
        signal: controller.signal,
      });

      let payload = null;
      try {
        payload = await response.json();
      } catch {
        payload = null;
      }

      if (!response.ok) {
        const detail = parseErrorDetail(payload);
        const kind = response.status === 422
          ? "request"
          : response.status === 502 || response.status === 503
            ? "service"
            : "http";
        throw new RagApiError(
          detail || `请求失败（HTTP ${response.status}）`,
          { kind, status: response.status, detail, payload },
        );
      }

      return payload;
    } catch (error) {
      if (error instanceof RagApiError) throw error;
      if (error && error.name === "AbortError") {
        throw new RagApiError("请求超时", { kind: "timeout" });
      }
      throw new RagApiError("无法连接本地 RAG 服务", { kind: "network" });
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function validateHealth(payload) {
    if (!isRecord(payload) || !["ok", "degraded"].includes(payload.status)) {
      throw new RagApiError("健康检查响应格式不完整", { kind: "contract" });
    }
    if (typeof payload.llm_configured !== "boolean") {
      throw new RagApiError("健康检查缺少 LLM 状态", { kind: "contract" });
    }
    return {
      status: payload.status,
      llmConfigured: payload.llm_configured,
    };
  }

  function validateCitation(value) {
    if (!isRecord(value) || !isString(value.evidence_id) || !value.evidence_id.trim()) {
      throw new RagApiError("引用响应格式不完整", { kind: "contract" });
    }
    return value;
  }

  function validateEvidence(value) {
    if (
      !isRecord(value)
      || !isString(value.evidence_id)
      || !isString(value.text)
      || !isRecord(value.citation)
    ) {
      throw new RagApiError("证据响应格式不完整", { kind: "contract" });
    }
    validateCitation(value.citation);
    return value;
  }

  function validateRetrieval(value) {
    const requiredNumbers = ["top_k", "hit_count"];
    if (
      !isRecord(value)
      || !isString(value.mode)
      || requiredNumbers.some((key) => !isFiniteNumber(value[key]))
      || !Array.isArray(value.retrieval_tasks)
      || !isRecord(value.planner)
    ) {
      throw new RagApiError("检索响应格式不完整", { kind: "contract" });
    }
    value.retrieval_tasks.forEach((task) => {
      if (
        !isRecord(task)
        || !isString(task.task_id)
        || !isRecord(task.requirement)
        || !Array.isArray(task.queries)
        || !Array.isArray(task.evidence_ids)
      ) {
        throw new RagApiError("检索任务响应格式不完整", { kind: "contract" });
      }
    });
    return value;
  }

  function validateRagResponse(payload) {
    if (
      !isRecord(payload)
      || !isString(payload.answer)
      || typeof payload.answerable !== "boolean"
      || typeof payload.citation_valid !== "boolean"
      || !Array.isArray(payload.citation_ids)
      || !payload.citation_ids.every(isString)
      || !Array.isArray(payload.citations)
      || !Array.isArray(payload.evidence)
      || !isRecord(payload.retrieval)
      || !isRecord(payload.generation)
    ) {
      throw new RagApiError("RAG 响应格式不完整", { kind: "contract" });
    }
    payload.citations.forEach(validateCitation);
    payload.evidence.forEach(validateEvidence);
    validateRetrieval(payload.retrieval);
    return payload;
  }

  async function health() {
    return validateHealth(await requestJson("/health", { timeoutMs: DEFAULT_TIMEOUTS.health }));
  }

  async function query(body) {
    return validateRagResponse(await requestJson("/api/rag/query", {
      method: "POST",
      body,
      timeoutMs: DEFAULT_TIMEOUTS.query,
    }));
  }

  window.RagApi = {
    RagApiError,
    getApiBase,
    health,
    query,
    validateHealth,
    validateRagResponse,
  };
})();
