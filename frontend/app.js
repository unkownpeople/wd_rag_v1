(() => {
  "use strict";

  const form = document.querySelector("#question-form");
  const input = document.querySelector("#question-input");
  const clearButton = document.querySelector("#clear-button");
  const submitButton = document.querySelector("#submit-button");
  const composerHint = document.querySelector("#composer-hint");
  const answerContent = document.querySelector("#answer-content");
  const answerState = document.querySelector("#answer-state");
  const answerNote = document.querySelector("#answer-note");
  const toast = document.querySelector("#toast");
  const citationCount = document.querySelector("#citation-count");
  const citationEmpty = document.querySelector("#citation-empty");
  const citationList = document.querySelector("#citation-list");
  const citationPanel = document.querySelector(".brief-citation-panel");
  const evidenceEmpty = document.querySelector("#evidence-empty");
  const evidenceState = document.querySelector("#evidence-state");
  const evidenceList = document.querySelector("#evidence-list");
  const evidencePanel = document.querySelector(".evidence-panel");
  const briefQuestion = document.querySelector("#brief-question");
  const exampleButtons = [...document.querySelectorAll(".example-button")];
  const stateButtons = [...document.querySelectorAll("[data-preview-state]")];
  const viewButtons = [...document.querySelectorAll("[data-view-target]")];
  const views = [...document.querySelectorAll("[data-view]")];
  const retrievalFields = [...document.querySelectorAll("[data-retrieval-field]")];
  const processFields = [...document.querySelectorAll("[data-process-field]")];

  const stateCopy = {
    initial: {
      label: "等待查询",
      title: "答案将在真实查询后显示",
      copy: "输入问题后，页面会调用本地 FastAPI，并只展示后端返回的答案、引用和证据。",
      note: "页面不会在浏览器中调用 DeepSeek，也不会伪造答案、数字或引用。",
    },
    "no-answer": {
      label: "无答案",
      title: "根据当前召回内容无法确定。",
      copy: "后端没有返回足够的证据支持一个可验证的结论。",
      note: "无答案状态保留真实召回结果，不生成猜测。",
    },
    "citation-invalid": {
      label: "引用未通过校验",
      title: "回答未通过引用校验",
      copy: "后端未确认答案中的引用与证据完整匹配，因此没有把它标记为已验证。",
      note: "引用编号缺失、重复或无法对应证据时，页面不会自行补齐引用。",
    },
    error: {
      label: "请求错误",
      title: "暂时无法完成这次查询",
      copy: "请检查本地 FastAPI 服务后重试。页面不会展示堆栈、Key 或服务器内部路径。",
      note: "错误状态只保留用户可执行的处理提示。",
    },
  };

  let toastTimer;
  let healthTimer;
  let loading = false;
  let evidenceElements = new Map();
  let lastSubmittedQuestion = "";

  function element(tagName, className, text) {
    const node = document.createElement(tagName);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function showToast(message) {
    window.clearTimeout(toastTimer);
    toast.textContent = message;
    toast.classList.add("is-visible");
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 3200);
  }

  function setComposerHint(message, isError = false) {
    composerHint.textContent = message;
    composerHint.classList.toggle("is-error", isError);
  }

  function setBriefQuestion(question) {
    lastSubmittedQuestion = String(question || "").trim();
    briefQuestion.textContent = lastSubmittedQuestion || "等待真实查询";
  }

  function setActiveExample(activeButton) {
    exampleButtons.forEach((button) => button.classList.toggle("is-selected", button === activeButton));
  }

  function setStateButton(activeState) {
    stateButtons.forEach((button) => {
      button.classList.toggle("is-active", button.dataset.previewState === activeState);
    });
  }

  function setView(nextView, updateUrl = true) {
    const viewName = nextView === "brief" ? "brief" : "qa";
    views.forEach((view) => {
      view.hidden = view.dataset.view !== viewName;
    });
    viewButtons.forEach((button) => {
      const isActive = button.dataset.viewTarget === viewName;
      button.classList.toggle("is-active", isActive);
      button.setAttribute("aria-pressed", String(isActive));
    });
    if (updateUrl) {
      const url = new URL(window.location.href);
      if (viewName === "qa") url.searchParams.delete("view");
      else url.searchParams.set("view", viewName);
      window.history.replaceState({}, "", url);
    }
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function resetAnswerMarkup() {
    clearNode(answerContent);
    const illustration = element("div", "empty-illustration");
    illustration.setAttribute("aria-hidden", "true");
    illustration.innerHTML = '<span class="empty-ring empty-ring--outer"></span><span class="empty-ring empty-ring--inner"></span><svg viewBox="0 0 36 36"><path d="m21.5 21.5 7 7M15.5 24a8.5 8.5 0 1 1 0-17 8.5 8.5 0 0 1 0 17Z" /></svg>';
    answerContent.append(illustration, element("h3"), element("p"));
    answerContent.querySelector("h3").id = "answer-empty-title";
    answerContent.querySelector("p").id = "answer-empty-copy";
  }

  function setAnswerState(nextState, customCopy = {}) {
    const copy = { ...(stateCopy[nextState] || stateCopy.initial), ...customCopy };
    answerContent.dataset.viewState = nextState;
    answerState.textContent = copy.label;
    resetAnswerMarkup();
    answerContent.querySelector("#answer-empty-title").textContent = copy.title;
    answerContent.querySelector("#answer-empty-copy").textContent = copy.copy;
    answerNote.textContent = "";
    const noteMark = element("span", "note-mark", "i");
    noteMark.setAttribute("aria-hidden", "true");
    answerNote.append(noteMark, element("span", "", copy.note));
    setStateButton(nextState);
  }

  function setLoading(isLoading) {
    loading = isLoading;
    submitButton.disabled = isLoading;
    clearButton.disabled = isLoading;
    submitButton.classList.toggle("is-loading", isLoading);
    if (isLoading) {
      answerContent.dataset.viewState = "loading";
      answerState.textContent = "请求中";
      clearNode(answerContent);
      answerContent.append(
        element("span", "loading-line"),
        element("span", "loading-line"),
        element("span", "loading-line loading-line--short"),
      );
      answerNote.textContent = "";
      const noteMark = element("span", "note-mark", "i");
      noteMark.setAttribute("aria-hidden", "true");
      answerNote.append(noteMark, element("span", "", "正在等待 FastAPI 返回，重复提交已暂时禁用。"));
      setStateButton("loading");
      setProcessState("loading");
      setComposerHint("正在查询本地 RAG…");
    } else if (!composerHint.classList.contains("is-error")) {
      setComposerHint("支持 Ctrl + Enter 提交");
    }
  }

  function setProcessState(state, response) {
    const values = {
      retrieval: "等待真实请求",
      coverage: "等待真实请求",
      generation: "等待真实请求",
    };
    if (state === "loading") {
      values.retrieval = "检索中";
      values.coverage = "等待检索结果";
      values.generation = "等待生成与校验";
    } else if (state === "error") {
      values.retrieval = "未完成";
      values.coverage = "未完成";
      values.generation = "未完成";
    } else if (response) {
      const retrieval = response.retrieval;
      const status = String(response.generation.status || "");
      const tasks = Array.isArray(retrieval.retrieval_tasks) ? retrieval.retrieval_tasks : [];
      const coveredTasks = tasks.filter((task) => Array.isArray(task.evidence_ids) && task.evidence_ids.length).length;
      values.retrieval = `${retrieval.mode || "检索"} · ${retrieval.hit_count} 条命中`;
      values.coverage = tasks.length
        ? `${coveredTasks}/${tasks.length} 项证据需求已覆盖`
        : response.answerable ? "已返回" : "证据不足";
      values.generation = response.citation_valid
        ? status === "retrieval_only" ? "未调用 LLM" : "生成与引用通过"
        : "引用校验未通过";
    }
    processFields.forEach((field) => {
      field.textContent = values[field.dataset.processField] || "—";
    });
  }

  function setRetrievalDetails(retrieval) {
    const tasks = Array.isArray(retrieval?.retrieval_tasks) ? retrieval.retrieval_tasks : [];
    const coveredTasks = tasks.filter((task) => Array.isArray(task.evidence_ids) && task.evidence_ids.length).length;
    const values = retrieval
      ? {
          mode: retrieval.mode,
          top_k: retrieval.top_k,
          hit_count: retrieval.hit_count,
          candidate_count: retrieval.candidate_count ?? 0,
          task_count: tasks.length,
          task_coverage: tasks.length ? `${coveredTasks}/${tasks.length}` : "—",
        }
      : {
          mode: "—",
          top_k: "—",
          hit_count: "—",
          candidate_count: "—",
          task_count: "—",
          task_coverage: "—",
        };
    retrievalFields.forEach((field) => {
      field.textContent = String(values[field.dataset.retrievalField] ?? "—");
    });
  }

  function setServiceStatus(kind, state, label) {
    document.querySelectorAll(`[data-status="${kind}"]`).forEach((item) => {
      item.classList.remove("status-item--checking", "status-item--offline", "status-item--online");
      if (state === "online") item.classList.add("status-item--online");
      else if (state === "checking") item.classList.add("status-item--checking");
      else item.classList.add("status-item--offline");
      const dot = item.querySelector(".status-dot");
      if (dot) dot.classList.toggle("status-dot--muted", state === "online");
      const text = item.querySelector(".status-label");
      if (text) text.textContent = label;
    });
  }

  async function refreshHealth() {
    setServiceStatus("api", "checking", "API 检查中");
    setServiceStatus("llm", "checking", "LLM 检查中");
    try {
      const health = await window.RagApi.health();
      setServiceStatus("api", "online", health.status === "ok" ? "API 已连接" : "API 已连接（降级）");
      setServiceStatus("llm", health.llmConfigured ? "online" : "offline", health.llmConfigured ? "LLM 已配置" : "LLM 未配置");
    } catch (error) {
      setServiceStatus("api", "offline", "API 未连接");
      setServiceStatus("llm", "offline", "LLM 状态未知");
    }
  }

  function sourceFileName(value) {
    if (!value || typeof value !== "string") return "";
    const parts = value.split(/[\\/]/);
    return parts[parts.length - 1] || "";
  }

  function locationText(citation) {
    const locations = [];
    if (citation.page_start || citation.page_end || citation.page) {
      const start = citation.page_start || citation.page;
      const end = citation.page_end && citation.page_end !== start ? `-${citation.page_end}` : "";
      locations.push(`第 ${start}${end} 页`);
    }
    if (citation.sheet_name) locations.push(`工作表 ${citation.sheet_name}`);
    if (citation.cell_range) locations.push(`单元格 ${citation.cell_range}`);
    if (citation.paragraph_index !== null && citation.paragraph_index !== undefined) {
      locations.push(`段落 ${citation.paragraph_index}`);
    }
    return locations.join(" · ");
  }

  function citationTitle(citation) {
    const primary = [citation.company_name, citation.fiscal_year && `${citation.fiscal_year} 年`]
      .filter(Boolean)
      .join(" · ");
    return primary || citation.table_title || citation.table_title_raw || "未命名来源";
  }

  function citationMeta(citation) {
    return [
      citation.source_format && citation.source_format.toUpperCase(),
      sourceFileName(citation.source_file),
      citation.source_revision && `版本 ${citation.source_revision}`,
      locationText(citation),
      citation.statement_family,
      citation.period_end,
      citation.statement_scope,
      citation.unit,
    ].filter(Boolean);
  }

  function evidenceMetadata(citation) {
    const entries = [
      ["来源文件", sourceFileName(citation.source_file)],
      ["来源版本", citation.source_revision],
      ["表格标题", citation.table_title],
      ["原始表题", citation.table_title_raw],
      ["表格组", citation.table_group_id],
      ["报表族", citation.statement_family],
      ["口径", citation.statement_scope],
      ["期间结束", citation.period_end],
      ["单位", citation.unit],
      ["结构化定位", locationText(citation)],
      ["来源定位", citation.source_location],
    ];
    return entries.filter(([, value]) => value !== null && value !== undefined && String(value).trim());
  }

  function safeDomId(value) {
    return `evidence-${String(value).replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  }

  function scrollToEvidence(evidenceId) {
    const target = evidenceElements.get(evidenceId);
    if (!target) {
      showToast("当前引用没有对应的证据原文。");
      return;
    }
    setView("brief");
    window.setTimeout(() => {
      target.scrollIntoView({ behavior: "smooth", block: "center" });
      target.classList.add("is-focused");
      window.setTimeout(() => target.classList.remove("is-focused"), 1400);
    }, 80);
  }

  function renderAnswerText(answer, citationMap, evidenceMap, citationValid) {
    const wrapper = element("div", "answer-result");
    const text = element("p", "answer-result-text");
    const appendFormattedText = (value) => {
      const inlinePattern = /(\*\*[^*\n]+\*\*|`[^`\n]+`)/g;
      String(value).split(inlinePattern).forEach((token) => {
        if (token.startsWith("**") && token.endsWith("**")) {
          text.appendChild(element("strong", "", token.slice(2, -2)));
        } else if (token.startsWith("`") && token.endsWith("`")) {
          text.appendChild(element("code", "answer-inline-code", token.slice(1, -1)));
        } else {
          text.appendChild(document.createTextNode(token.replaceAll("`", "")));
        }
      });
    };
    const tokenPattern = /(\[[^\]\n]{1,80}\])/g;
    const parts = String(answer).split(tokenPattern);
    parts.forEach((part) => {
      if (!/^\[[^\]]+\]$/.test(part)) {
        appendFormattedText(part);
        return;
      }
      const id = part.slice(1, -1);
      if (citationValid && citationMap.has(id) && evidenceMap.has(id)) {
        const button = element("button", "citation-link", part);
        button.type = "button";
        button.title = `定位到 ${id} 的证据`;
        button.addEventListener("click", () => scrollToEvidence(id));
        text.appendChild(button);
      } else {
        const invalid = element("span", "citation-link citation-link--invalid", part);
        invalid.title = "该引用未能匹配到证据";
        text.appendChild(invalid);
      }
    });
    wrapper.appendChild(text);
    return wrapper;
  }

  function renderCitations(citations) {
    clearNode(citationList);
    citationCount.textContent = `${citations.length} 个来源`;
    citationEmpty.hidden = citations.length > 0;
    citationPanel.classList.toggle("has-scroll-content", citations.length > 0);
    citationList.tabIndex = citations.length > 0 ? 0 : -1;
    citations.forEach((citation) => {
      const card = element("article", "citation-card");
      const button = element("button", "citation-card__button");
      button.type = "button";
      button.addEventListener("click", () => scrollToEvidence(citation.evidence_id));
      const heading = element("span", "citation-card__title", `[${citation.evidence_id}] ${citationTitle(citation)}`);
      const location = element("span", "citation-card__location", locationText(citation) || "定位信息随证据返回");
      const meta = element("span", "citation-card__meta");
      citationMeta(citation).forEach((item) => meta.appendChild(element("span", "citation-chip", item)));
      button.append(heading, location, meta);
      card.appendChild(button);
      citationList.appendChild(card);
    });
  }

  function renderEvidence(evidence) {
    clearNode(evidenceList);
    evidenceElements = new Map();
    evidenceEmpty.hidden = evidence.length > 0;
    evidencePanel.classList.toggle("has-scroll-content", evidence.length > 0);
    evidenceList.tabIndex = evidence.length > 0 ? 0 : -1;
    if (evidenceState) {
      evidenceState.textContent = evidence.length ? `${evidence.length} 条证据` : "尚无证据";
      evidenceState.classList.toggle("state-label--quiet", evidence.length === 0);
    }
    evidence.forEach((item) => {
      const citation = item.citation;
      const card = element("article", "evidence-card");
      card.id = safeDomId(item.evidence_id);
      evidenceElements.set(item.evidence_id, card);
      const heading = element("div", "evidence-card__heading");
      heading.append(
        element("span", "evidence-card__id", `[${item.evidence_id}]`),
        element("h3", "evidence-card__title", citationTitle(citation)),
      );
      const meta = element("div", "evidence-card__meta");
      citationMeta(citation).forEach((value) => meta.appendChild(element("span", "citation-chip", value)));
      const metadata = evidenceMetadata(citation);
      const details = element("dl", "evidence-card__details");
      metadata.forEach(([label, value]) => {
        const row = element("div", "evidence-detail");
        row.append(element("dt", "evidence-detail__label", label), element("dd", "evidence-detail__value", String(value)));
        details.appendChild(row);
      });
      card.append(heading, meta);
      if (metadata.length) card.appendChild(details);
      card.appendChild(element("p", "evidence-card__text", item.text));
      evidenceList.appendChild(card);
    });
  }

  function renderCollections(response) {
    const citationMap = new Map(response.citations.map((item) => [item.evidence_id, item]));
    const evidenceMap = new Map(response.evidence.map((item) => [item.evidence_id, item]));
    const citations = response.citations.length
      ? response.citations
      : response.evidence.map((item) => item.citation);
    renderCitations([...new Map(citations.map((item) => [item.evidence_id, item])).values()]);
    renderEvidence(response.evidence);
    return { citationMap, evidenceMap };
  }

  function citationIntegrity(response) {
    const citationIds = response.citation_ids;
    const citationMap = new Map(response.citations.map((item) => [item.evidence_id, item]));
    const evidenceMap = new Map(response.evidence.map((item) => [item.evidence_id, item]));
    const duplicates = citationIds.filter((id, index) => citationIds.indexOf(id) !== index);
    const missing = citationIds.filter((id) => !citationMap.has(id) || !evidenceMap.has(id));
    const answerIds = [...response.answer.matchAll(/\[([^\]\n]{1,80})\]/g)].map((match) => match[1]);
    const missingAnswerIds = answerIds.filter((id) => !citationMap.has(id) || !evidenceMap.has(id));
    return {
      citationMap,
      evidenceMap,
      valid: response.citation_valid && !duplicates.length && !missing.length && !missingAnswerIds.length,
    };
  }

  function renderResponse(response) {
    const integrity = citationIntegrity(response);
    renderCollections(response);
    setRetrievalDetails(response.retrieval);
    setProcessState("result", response);
    clearNode(answerContent);
    answerContent.dataset.viewState = response.answerable && integrity.valid ? "success" : response.answerable ? "citation-invalid" : "no-answer";
    answerContent.appendChild(renderAnswerText(response.answer, integrity.citationMap, integrity.evidenceMap, integrity.valid));
    if (response.answerable && integrity.valid) {
      answerState.textContent = "引用已验证";
      setStateButton("success");
      setComposerHint("查询完成，可继续提问");
      answerNote.textContent = "";
      const mark = element("span", "note-mark", "✓");
      mark.setAttribute("aria-hidden", "true");
      answerNote.append(mark, element("span", "", "答案、引用、证据和检索详情均来自本次后端响应。"));
      return;
    }
    if (!response.answerable) {
      answerState.textContent = "无答案";
      setStateButton("no-answer");
      setComposerHint("当前证据不足，可换一个问题重试");
      answerNote.textContent = "";
      const mark = element("span", "note-mark", "i");
      mark.setAttribute("aria-hidden", "true");
      answerNote.append(mark, element("span", "", "保留真实证据，不根据相似内容猜测。"));
      return;
    }
    answerState.textContent = "引用未通过校验";
    setStateButton("citation-invalid");
    setComposerHint("引用未完整匹配，请检查后端响应", true);
    answerNote.textContent = "";
    const mark = element("span", "note-mark", "!");
    mark.setAttribute("aria-hidden", "true");
    answerNote.append(mark, element("span", "", "页面没有自行补齐或隐藏未匹配的引用。"));
  }

  function errorCopy(error) {
    if (error?.kind === "request") return { title: "问题参数不符合接口要求", copy: "请确认问题不为空，并检查输入长度后重试。" };
    if (error?.kind === "service") return { title: "本地 RAG 服务暂时不可用", copy: "请检查 FastAPI、向量库配置和 LLM 服务状态。" };
    if (error?.kind === "timeout") return { title: "查询超时", copy: "本次请求没有自动重复发送，请确认本地服务状态后再试。" };
    if (error?.kind === "contract") return { title: "后端响应格式无法识别", copy: "页面已停止展示这次响应，请检查前后端接口契约。" };
    return { title: "无法连接本地 RAG 服务", copy: "请确认前端代理和 FastAPI 服务都已启动后重试。" };
  }

  function renderError(error) {
    renderCitations([]);
    renderEvidence([]);
    setRetrievalDetails(null);
    setProcessState("error");
    const copy = errorCopy(error);
    setAnswerState("error", copy);
    setComposerHint(copy.title, true);
    showToast(copy.title);
  }

  function resetResult() {
    setBriefQuestion("");
    renderCitations([]);
    renderEvidence([]);
    setRetrievalDetails(null);
    setProcessState("initial");
    setAnswerState("initial");
  }

  async function submitQuery() {
    const query = input.value.trim();
    if (!query) {
      setComposerHint("请先输入一个年报问题", true);
      input.focus();
      return;
    }
    if (loading) return;
    setBriefQuestion(query);
    setLoading(true);
    renderCitations([]);
    renderEvidence([]);
    setRetrievalDetails(null);
    try {
      const response = await window.RagApi.query({
        query,
        top_k: 5,
        mode: "hybrid",
        generate: true,
      });
      renderResponse(response);
    } catch (error) {
      renderError(error);
    } finally {
      setLoading(false);
    }
  }

  exampleButtons.forEach((button) => {
    button.addEventListener("click", () => {
      input.value = button.dataset.question || "";
      setActiveExample(button);
      setComposerHint("示例问题已填入，可直接提交");
      input.focus();
    });
  });

  input.addEventListener("input", () => {
    if (!input.value.trim()) setActiveExample(null);
    if (composerHint.classList.contains("is-error")) setComposerHint("支持 Ctrl + Enter 提交");
  });

  clearButton.addEventListener("click", () => {
    input.value = "";
    setActiveExample(null);
    resetResult();
    setComposerHint("支持 Ctrl + Enter 提交");
    input.focus();
  });

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitQuery();
  });

  input.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      form.requestSubmit();
    }
  });

  stateButtons.forEach((button) => {
    button.addEventListener("click", () => {
      if (loading) return;
      const previewState = button.dataset.previewState;
      setAnswerState(previewState);
      showToast(`已切换到${button.textContent}状态，仅用于视觉回归检查。`);
    });
  });

  viewButtons.forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  });

  setView(new URLSearchParams(window.location.search).get("view"), false);
  resetResult();
  refreshHealth();
  healthTimer = window.setInterval(refreshHealth, 30000);
  window.addEventListener("online", refreshHealth);
})();
