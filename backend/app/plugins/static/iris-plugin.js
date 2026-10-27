/*
 * Iris 插件页面 SDK。页面运行在 CSP 沙箱里，通过宿主（管理后台）拿到插件令牌和主题。
 *
 * 宿主 → 页面：{type: "iris:init", pluginId, token, theme, locale}
 *              {type: "iris:token", requestId, token}
 * 页面 → 宿主：{type: "iris:ready"} {type: "iris:toast", message}
 *              {type: "iris:refresh-token", requestId}
 *
 * Web 端宿主是父窗口（iframe）；Android 端宿主注入 IrisHost 通道，并在页面里调用 window.postMessage。
 */
(function () {
  "use strict";

  var state = { pluginId: null, token: null, locale: null, theme: {} };
  var pendingTokens = {};
  var nextRequestId = 1;
  var resolveReady;
  var ready = new Promise(function (resolve) {
    resolveReady = resolve;
  });

  function send(message) {
    if (window.IrisHost && typeof window.IrisHost.postMessage === "function") {
      window.IrisHost.postMessage(JSON.stringify(message));
    } else if (window.parent !== window) {
      // 宿主页面的源因部署而异，沙箱页面无法得知，只能用 *；消息里不含敏感内容
      window.parent.postMessage(message, "*");
    }
  }

  function applyTheme(theme) {
    var root = document.documentElement;
    Object.keys(theme || {}).forEach(function (name) {
      if (/^[a-z0-9-]+$/.test(name) && typeof theme[name] === "string") {
        root.style.setProperty("--iris-" + name, theme[name]);
      }
    });
  }

  window.addEventListener("message", function (event) {
    // 只接受宿主的消息：iframe 里是父窗口；Android 顶层页面里 parent 就是自身
    if (event.source !== window.parent) return;
    var data = event.data;
    if (typeof data === "string") {
      try {
        data = JSON.parse(data);
      } catch (error) {
        return;
      }
    }
    if (!data || typeof data.type !== "string") return;
    if (data.type === "iris:init") {
      state.pluginId = data.pluginId;
      state.token = data.token;
      state.locale = data.locale || null;
      state.theme = data.theme || {};
      applyTheme(state.theme);
      resolveReady(api);
    } else if (data.type === "iris:token" && pendingTokens[data.requestId]) {
      state.token = data.token;
      pendingTokens[data.requestId](data.token);
      delete pendingTokens[data.requestId];
    }
  });

  function refreshToken() {
    return new Promise(function (resolve, reject) {
      var requestId = String(nextRequestId++);
      pendingTokens[requestId] = resolve;
      send({ type: "iris:refresh-token", requestId: requestId });
      setTimeout(function () {
        if (pendingTokens[requestId]) {
          delete pendingTokens[requestId];
          reject(new Error("宿主没有响应令牌刷新"));
        }
      }, 10000);
    });
  }

  /* 请求插件自己的接口；path 相对于 /api/plugins/{pluginId}/。options.json 会序列化为请求体。 */
  function request(path, options, retried) {
    return ready.then(function () {
      var init = Object.assign({}, options || {});
      var headers = new Headers(init.headers || {});
      headers.set("Authorization", "Bearer " + state.token);
      if (init.json !== undefined) {
        headers.set("Content-Type", "application/json");
        init.body = JSON.stringify(init.json);
        delete init.json;
      }
      init.headers = headers;
      init.credentials = "omit";
      var url = "/api/plugins/" + state.pluginId + "/" + String(path).replace(/^\/+/, "");
      return fetch(url, init).then(function (response) {
        if (response.status === 401 && !retried) {
          return refreshToken().then(function () {
            return request(path, options, true);
          });
        }
        return response;
      });
    });
  }

  /* 和 request 相同，但解析 JSON；失败时抛出带服务端错误信息的 Error。 */
  function json(path, options) {
    return request(path, options).then(function (response) {
      if (response.status === 204) return null;
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (body) {
          if (response.ok) return body;
          var error = new Error(
            (body && body.error && body.error.message) || "请求失败（" + response.status + "）"
          );
          error.status = response.status;
          error.code = body && body.error && body.error.code;
          throw error;
        });
    });
  }

  function element(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  function makeButton(label, className, type) {
    var node = element("button", className);
    node.type = type || "button";
    node.textContent = label;
    return node;
  }

  /*
   * 渲染插件设置表单。服务端约定：GET/PUT path 返回 {fields, values, testable}，
   * POST path + "/test" 返回 {ok, message}。secret 的值只有 {set: true/false}。
   *
   * options: {path, title, description, onSaved(values)}
   * 返回表单可用后 resolve 的 Promise；加载失败时已经显示错误并 resolve null。
   */
  function settingsForm(container, options) {
    options = options || {};
    var target =
      typeof container === "string" ? document.querySelector(container) : container;
    if (!target) return Promise.reject(new Error("没有找到设置页容器"));

    var path = String(options.path || "settings").replace(/^\/+|\/+$/g, "");
    var form = element("form", "iris-card iris-settings-form");
    var fieldsBox = element("div");
    var actions = element("div", "iris-actions");
    var saveButton = makeButton("保存", "", "submit");
    var testButton = null;
    var status = element("p", "iris-form-status");
    var rows = [];
    var busy = true;
    var iris = null;

    status.setAttribute("role", "status");
    if (options.title) {
      var heading = element("h2");
      heading.textContent = String(options.title);
      form.appendChild(heading);
    }
    if (options.description) {
      var description = element("p", "iris-muted");
      description.textContent = String(options.description);
      form.appendChild(description);
    }
    actions.appendChild(saveButton);
    form.appendChild(fieldsBox);
    form.appendChild(actions);
    form.appendChild(status);
    target.textContent = "";
    target.appendChild(form);

    function showStatus(message, kind) {
      status.textContent = message || "";
      status.className = "iris-form-status" + (kind ? " iris-" + kind : "");
    }

    function setBusy(value) {
      busy = value;
      Array.prototype.forEach.call(
        form.querySelectorAll("input, select, button"),
        function (node) {
          node.disabled = value;
        }
      );
      if (!value) {
        rows.forEach(function (row) {
          if (row.field.kind === "secret") updateClear(row);
        });
      }
    }

    function readField(row) {
      var field = row.field;
      if (field.kind === "bool") return row.input.checked;
      if (field.kind === "number") {
        var raw = row.input.value.trim();
        if (raw === "") return null;
        var parsed = Number(raw);
        // 浏览器允许输入 1.2.3 之类的文本；原样交给服务端报错
        return isNaN(parsed) ? raw : parsed;
      }
      return row.input.value;
    }

    function applyField(row, value) {
      var field = row.field;
      if (field.kind === "bool") {
        row.input.checked = value === true;
      } else {
        row.input.value = value === null || value === undefined ? "" : String(value);
        if (field.kind === "select" && row.input.selectedIndex < 0) {
          row.input.selectedIndex = 0;
        }
      }
    }

    function updateClear(row) {
      row.clearButton.disabled =
        busy || row.field.required || !(row.initialSet || row.input.value !== "");
    }

    function setSecret(row, isSet) {
      row.initialSet = isSet;
      row.clearRequested = false;
      row.input.value = "";
      row.input.type = "password";
      row.toggle.textContent = "显示";
      row.input.placeholder = isSet ? "已设置，留空保持不变" : "未设置";
      updateClear(row);
    }

    function createSecret(row, controlId, box) {
      var wrap = element("div", "iris-secret");
      var input = element("input", "iris-secret-input");
      input.type = "password";
      input.id = controlId;
      input.autocomplete = "new-password";
      var secretActions = element("div", "iris-secret-actions");
      var toggle = makeButton("显示", "iris-secondary iris-small");
      var clear = makeButton("清除", "iris-secondary iris-small");
      row.input = input;
      row.toggle = toggle;
      row.clearButton = clear;
      toggle.addEventListener("click", function () {
        var visible = input.type === "password";
        input.type = visible ? "text" : "password";
        toggle.textContent = visible ? "隐藏" : "显示";
      });
      input.addEventListener("input", function () {
        if (input.value !== "") row.clearRequested = false;
        if (!row.initialSet || input.value !== "") {
          input.placeholder = row.initialSet ? "已设置，留空保持不变" : "未设置";
        }
        updateClear(row);
      });
      clear.addEventListener("click", function () {
        input.value = "";
        row.clearRequested = row.initialSet;
        input.placeholder = row.initialSet ? "已清除，保存后生效" : "未设置";
        updateClear(row);
      });
      secretActions.appendChild(toggle);
      secretActions.appendChild(clear);
      wrap.appendChild(input);
      wrap.appendChild(secretActions);
      box.appendChild(wrap);
      setSecret(row, false);
    }

    function createField(field, values) {
      var row = {
        field: field,
        input: null,
        initial: null,
        initialSet: false,
        clearRequested: false,
        toggle: null,
        clearButton: null,
      };
      var box = element("div", "iris-field");
      var controlId = "iris-setting-" + field.name;

      if (field.kind === "bool") {
        var checkLabel = element("label", "iris-checkbox");
        var check = element("input", "iris-checkbox-input");
        check.type = "checkbox";
        check.id = controlId;
        check.checked = values[field.name] === true;
        var checkText = element("span");
        checkText.textContent = field.label;
        checkLabel.appendChild(check);
        checkLabel.appendChild(checkText);
        box.appendChild(checkLabel);
        row.input = check;
        row.initial = check.checked;
      } else {
        var label = element("label");
        label.setAttribute("for", controlId);
        label.textContent = field.label;
        if (field.required) {
          var requiredMark = element("span", "iris-required");
          requiredMark.textContent = " *";
          label.appendChild(requiredMark);
        }
        box.appendChild(label);

        if (field.kind === "secret") {
          createSecret(row, controlId, box);
          setSecret(row, !!(values[field.name] && values[field.name].set));
        } else if (field.kind === "select") {
          var select = element("select", "iris-select");
          select.id = controlId;
          (field.options || []).forEach(function (option) {
            var node = element("option");
            node.value = option;
            node.textContent = option;
            select.appendChild(node);
          });
          row.input = select;
          applyField(row, values[field.name]);
          box.appendChild(select);
          row.initial = readField(row);
        } else {
          var input = element("input", "iris-" + field.kind);
          input.id = controlId;
          input.type = field.kind === "number" ? "number" : field.kind;
          if (field.kind === "number") {
            input.step = "any";
            if (field.min !== null && field.min !== undefined) {
              input.min = String(field.min);
            }
            if (field.max !== null && field.max !== undefined) {
              input.max = String(field.max);
            }
          } else if (field.kind === "text" || field.kind === "url") {
            if (field.max_length) input.maxLength = field.max_length;
            input.autocomplete = "off";
          }
          row.input = input;
          applyField(row, values[field.name]);
          box.appendChild(input);
          row.initial = readField(row);
        }
      }
      if (field.help) {
        var help = element("p", "iris-field-help");
        help.textContent = field.help;
        box.appendChild(help);
      }
      fieldsBox.appendChild(box);
      rows.push(row);
    }

    function render(data) {
      rows = [];
      fieldsBox.textContent = "";
      (data && data.fields ? data.fields : []).forEach(function (field) {
        createField(field, (data && data.values) || {});
      });
      if (data && data.testable) {
        if (!testButton) {
          testButton = makeButton("测试连接", "iris-secondary");
          testButton.addEventListener("click", testSettings);
          actions.appendChild(testButton);
        }
      } else if (testButton) {
        actions.removeChild(testButton);
        testButton = null;
      }
      setBusy(false);
    }

    function dirtyValues() {
      var body = {};
      rows.forEach(function (row) {
        var name = row.field.name;
        if (row.field.kind === "secret") {
          if (row.clearRequested) body[name] = null;
          else if (row.input.value !== "") body[name] = row.input.value;
          return;
        }
        var value = readField(row);
        if (value !== row.initial) body[name] = value;
      });
      return body;
    }

    function applyValues(values) {
      values = values || {};
      rows.forEach(function (row) {
        var name = row.field.name;
        if (row.field.kind === "secret") {
          var secret = values[name];
          setSecret(row, !!(secret && secret.set));
        } else {
          applyField(row, values[name]);
          row.initial = readField(row);
        }
      });
    }

    function saveSettings() {
      if (busy) return;
      var body = dirtyValues();
      if (Object.keys(body).length === 0) {
        showStatus("没有需要保存的改动");
        return;
      }
      setBusy(true);
      showStatus("保存中…");
      iris.json(path, { method: "PUT", json: body }).then(
        function (data) {
          applyValues(data && data.values);
          setBusy(false);
          showStatus("已保存", "success");
          iris.toast("已保存");
          if (typeof options.onSaved === "function") {
            options.onSaved(data && data.values);
          }
        },
        function (error) {
          setBusy(false);
          var message = (error && error.message) || "保存失败";
          showStatus(message, "error");
          iris.toast(message);
        }
      );
    }

    function testSettings() {
      if (busy) return;
      setBusy(true);
      showStatus("测试中…");
      iris.json(path + "/test", { method: "POST", json: dirtyValues() }).then(
        function (result) {
          setBusy(false);
          if (result && result.ok) {
            showStatus("连接成功", "success");
            iris.toast("连接成功");
          } else {
            var message = (result && result.message) || "测试失败";
            showStatus(message, "error");
            iris.toast(message);
          }
        },
        function (error) {
          setBusy(false);
          var message = (error && error.message) || "测试失败";
          showStatus(message, "error");
          iris.toast(message);
        }
      );
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      saveSettings();
    });
    setBusy(true);
    showStatus("加载中…");
    return IrisPlugin.ready()
      .then(function (plugin) {
        iris = plugin;
        return iris.json(path);
      })
      .then(
        function (data) {
          render(data);
          showStatus("");
          return { values: data && data.values };
        },
        function (error) {
          var message = (error && error.message) || "加载设置失败";
          showStatus(message, "error");
          IrisPlugin.toast(message);
          return null;
        }
      );
  }

  var api = {
    ready: function () {
      return ready;
    },
    fetch: request,
    json: json,
    settingsForm: settingsForm,
    toast: function (message) {
      send({ type: "iris:toast", message: String(message) });
    },
    get pluginId() {
      return state.pluginId;
    },
    get locale() {
      return state.locale;
    },
    get theme() {
      return Object.assign({}, state.theme);
    },
  };

  window.IrisPlugin = api;
  send({ type: "iris:ready" });
})();
