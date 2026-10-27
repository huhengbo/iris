(function () {
  "use strict";

  var iris = null;
  var status = null;
  var account = null;
  var qrSession = null;
  var qrTimer = null;
  var pollToken = 0;
  var starting = false;
  var savingCookie = false;
  var signingOut = false;

  function byId(id) {
    return document.getElementById(id);
  }

  function show(node, visible) {
    if (!node) return;
    node.hidden = !visible;
  }

  function setText(id, text) {
    var node = byId(id);
    if (node) node.textContent = text === null || text === undefined ? "" : String(text);
  }

  function messageOf(error) {
    return (error && error.message) || "请求失败，请稍后重试";
  }

  function formatTime(value) {
    if (!value) return "";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function formatSpace(bytes) {
    var value = Number(bytes) || 0;
    var tb = 1024 * 1024 * 1024 * 1024;
    var gb = 1024 * 1024 * 1024;
    var mb = 1024 * 1024;
    if (value >= tb) return (value / tb).toFixed(1) + " TB";
    if (value >= gb) return (value / gb).toFixed(1) + " GB";
    if (value >= mb) return (value / mb).toFixed(1) + " MB";
    return value + " B";
  }

  function connected() {
    return !!(status && status.credential && status.credential.state !== "missing");
  }

  function invalid() {
    return !!(status && status.credential && status.credential.state === "invalid");
  }

  function renderStatus() {
    var credential = (status && status.credential) || {};
    var rate = (status && status.rate_limit) || {};
    var isConnected = credential.state && credential.state !== "missing";
    var isInvalid = credential.state === "invalid";
    var facts = [];

    if (credential.device_label) facts.push("设备身份：" + credential.device_label);
    if (credential.saved_at) facts.push("登录于 " + formatTime(credential.saved_at));

    show(byId("account-card"), isConnected);
    show(byId("intro"), !isConnected);
    show(byId("invalid-notice"), isInvalid);
    show(byId("rate-notice"), !!rate.limited);

    setText("account-name", credential.user_name || "115 账号");
    setText("account-facts", facts.join(" · "));
    setText("login-title", isConnected ? "重新登录" : "扫码登录");
    show(byId("sign-out"), isConnected);

    // 两组接口的熔断状态总是都显示，受限的那组附上预计恢复时间
    setText(
      "api-status",
      (rate.apis || [])
        .map(function (api) {
          if (!api.limited) return api.label + "正常";
          var resume = api.resume_at ? "，预计 " + formatTime(api.resume_at) + " 恢复" : "";
          return api.label + "受限" + resume;
        })
        .join(" · ")
    );

    if (rate.limited) {
      var apis = (rate.apis || []).filter(function (api) {
        return api.limited;
      });
      var effects = { web: "同步暂停", app: "播放取不到直链" };
      var parts = apis.map(function (api) {
        var resume = api.resume_at ? "，预计 " + formatTime(api.resume_at) + " 恢复" : "";
        return api.label + "被暂停" + resume + "（期间" + (effects[api.id] || "相关功能不可用") + "）";
      });
      setText(
        "rate-text",
        "检测到 115 风控：" +
          (parts.length ? parts.join("；") : "已暂停 115 请求") +
          "。另一组接口不受影响，不需要重新登录。"
      );
    }

    if (isConnected && !isInvalid) {
      loadAccount();
    } else {
      account = null;
      show(byId("account-details"), false);
      setText("account-error", "");
    }
  }

  function renderAccount() {
    if (!account) return;
    var vip = account.is_vip
      ? "VIP" + (account.vip_expires ? "，" + account.vip_expires + " 到期" : "")
      : "非 VIP";
    var total = Number(account.space_total) || 0;
    var used = Number(account.space_used) || 0;
    var ratio = total > 0 ? Math.min(1, used / total) : 0;
    var bar = byId("space-bar");

    setText("account-vip", vip);
    setText(
      "space-text",
      "已用 " + formatSpace(used) + "，共 " + formatSpace(total)
    );
    if (bar) bar.value = ratio;
    show(byId("account-details"), true);
    setText("account-error", "");
  }

  function loadAccount() {
    setText("account-error", "正在读取账号信息…");
    iris.json("account").then(
      function (data) {
        account = data;
        renderAccount();
      },
      function (error) {
        account = null;
        show(byId("account-details"), false);
        setText("account-error", "账号信息读取失败：" + messageOf(error));
      }
    );
  }

  function loadStatus() {
    return iris.json("status").then(
      function (data) {
        status = data;
        renderStatus();
        return data;
      },
      function (error) {
        setText("login-error", "读取 115 状态失败：" + messageOf(error));
        return null;
      }
    );
  }

  function loadDevices() {
    var select = byId("device");
    return iris.json("devices").then(
      function (devices) {
        select.textContent = "";
        var recommended = null;
        for (var i = 0; i < devices.length; i++) {
          var device = devices[i];
          var option = document.createElement("option");
          option.value = device.id;
          option.textContent = device.recommended
            ? device.label + "（推荐）"
            : device.label;
          option.setAttribute("data-note", device.note || "");
          select.appendChild(option);
          if (device.recommended) recommended = device.id;
        }
        if (recommended) select.value = recommended;
        else if (devices.length > 0) select.value = devices[0].id;
        updateDeviceNote();
      },
      function (error) {
        setText("login-error", "读取设备身份失败：" + messageOf(error));
      }
    );
  }

  function updateDeviceNote() {
    var select = byId("device");
    var index = select.selectedIndex;
    if (index < 0) {
      setText("device-note", "");
      return;
    }
    var option = select.options[index];
    var note = option.getAttribute("data-note") || "";
    setText("device-note", note);
  }

  function setLoginBusy(busy) {
    starting = busy;
    var start = byId("qr-start");
    var device = byId("device");
    start.disabled = busy;
    start.textContent = busy ? "获取中…" : qrSession ? "重新获取" : "获取二维码";
    device.disabled = busy || !!qrSession;
  }

  function clearQr(cancelServer) {
    pollToken += 1;
    if (qrTimer) {
      clearTimeout(qrTimer);
      qrTimer = null;
    }
    var session = qrSession;
    qrSession = null;
    show(byId("qr-panel"), false);
    show(byId("login-actions"), true);
    setLoginBusy(false);
    setText("qr-status-text", "");
    if (cancelServer && session) {
      iris.request("qr-login/" + encodeURIComponent(session), {
        method: "DELETE",
        keepalive: true,
      }).catch(function () {
        // 页面已经离开或会话已结束时服务端会返回 404，不需要打扰管理员
      });
    }
  }

  function stopForUnload() {
    if (qrTimer) {
      clearTimeout(qrTimer);
      qrTimer = null;
    }
    var session = qrSession;
    qrSession = null;
    pollToken += 1;
    if (session && iris) {
      iris.request("qr-login/" + encodeURIComponent(session), {
        method: "DELETE",
        keepalive: true,
      }).catch(function () {
        return undefined;
      });
    }
  }

  function startQr() {
    if (starting || !iris) return;
    var select = byId("device");
    if (select.selectedIndex < 0) {
      setText("login-error", "请选择设备身份");
      return;
    }
    var device = select.value;
    clearQr(true);
    var token = pollToken;
    setText("login-error", "");
    setText("cookie-error", "");
    setLoginBusy(true);
    iris.json("qr-login", { method: "POST", json: { device: device } }).then(
      function (login) {
        if (token !== pollToken) {
          // 请求期间页面已经取消或离开，把这个新会话也收掉
          if (login && login.session_id) {
            iris
              .request("qr-login/" + encodeURIComponent(login.session_id), {
                method: "DELETE",
                keepalive: true,
              })
              .catch(function () {
                return undefined;
              });
          }
          return;
        }
        if (!login || !login.session_id) {
          throw new Error("服务端没有返回二维码");
        }
        qrSession = login.session_id;
        byId("qr-image").src =
          "data:image/png;base64," + String(login.image_base64 || "");
        show(byId("qr-panel"), true);
        show(byId("login-actions"), false);
        setLoginBusy(false);
        setText("qr-status-text", "请用 115 App 扫码");
        pollQr(login.session_id, token);
      },
      function (error) {
        if (token !== pollToken) return;
        setLoginBusy(false);
        setText("login-error", messageOf(error));
        if (error && error.code === "resource_rate_limited") loadStatus();
      }
    );
  }

  function schedulePoll(session, token) {
    qrTimer = setTimeout(function () {
      pollQr(session, token);
    }, 1000);
  }

  function pollQr(session, token) {
    if (!qrSession || qrSession !== session || pollToken !== token) return;
    iris.json("qr-login/" + encodeURIComponent(session)).then(
      function (progress) {
        if (!qrSession || qrSession !== session || pollToken !== token) return;
        setText("qr-status-text", progress.message || "请用 115 App 扫码");
        if (progress.status === "succeeded") {
          var name = progress.account && progress.account.user_name;
          clearQr(false);
          iris.toast(name ? "已登录 115：" + name : "115 登录成功");
          refreshAll();
          return;
        }
        if (progress.status === "expired" || progress.status === "cancelled") {
          clearQr(false);
          setText("login-error", progress.message || "二维码已失效，请重新获取");
          return;
        }
        schedulePoll(session, token);
      },
      function (error) {
        if (!qrSession || qrSession !== session || pollToken !== token) return;
        clearQr(false);
        setText("login-error", messageOf(error));
        if (error && error.code === "resource_rate_limited") loadStatus();
      }
    );
  }

  function toggleCookie(visible) {
    show(byId("cookie-panel"), visible);
    if (!visible) {
      byId("cookie-input").value = "";
      setText("cookie-error", "");
    }
  }

  function saveCookie() {
    if (savingCookie || !iris) return;
    var input = byId("cookie-input");
    var cookie = input.value.trim();
    var button = byId("cookie-submit");
    if (!cookie) {
      setText("cookie-error", "请粘贴 Cookie");
      return;
    }
    savingCookie = true;
    button.disabled = true;
    button.textContent = "校验中…";
    setText("cookie-error", "");
    iris
      .json("cookie", { method: "PUT", json: { cookie: cookie } })
      .then(
        function (data) {
          input.value = "";
          toggleCookie(false);
          iris.toast("已接入 115 账号：" + data.user_name);
          refreshAll();
        },
        function (error) {
          setText("cookie-error", messageOf(error));
        }
      )
      .then(function () {
        savingCookie = false;
        button.disabled = false;
        button.textContent = "校验并保存";
      });
  }

  function signOut() {
    if (signingOut || !iris) return;
    var button = byId("sign-out-yes");
    signingOut = true;
    button.disabled = true;
    button.textContent = "退出中…";
    iris.json("credential", { method: "DELETE" }).then(
      function () {
        show(byId("sign-out-confirm"), false);
        iris.toast("已退出 115 账号");
        refreshAll();
      },
      function (error) {
        setText("account-error", messageOf(error));
      }
    ).then(function () {
      signingOut = false;
      button.disabled = false;
      button.textContent = "确认退出";
    });
  }

  function refreshAll() {
    show(byId("sign-out-confirm"), false);
    setText("login-error", "");
    return loadStatus();
  }

  function bind() {
    byId("refresh").addEventListener("click", refreshAll);
    byId("device").addEventListener("change", function () {
      updateDeviceNote();
    });
    byId("qr-start").addEventListener("click", startQr);
    byId("qr-cancel").addEventListener("click", function () {
      clearQr(true);
      setText("login-error", "");
    });
    byId("cookie-toggle").addEventListener("click", function () {
      clearQr(true);
      toggleCookie(byId("cookie-panel").hidden);
    });
    byId("cookie-cancel").addEventListener("click", function () {
      toggleCookie(false);
    });
    byId("cookie-submit").addEventListener("click", saveCookie);
    byId("sign-out").addEventListener("click", function () {
      show(byId("sign-out-confirm"), true);
    });
    byId("sign-out-cancel").addEventListener("click", function () {
      show(byId("sign-out-confirm"), false);
    });
    byId("sign-out-yes").addEventListener("click", signOut);
    window.addEventListener("beforeunload", stopForUnload);
  }

  IrisPlugin.ready()
    .then(function (plugin) {
      iris = plugin;
      bind();
      return Promise.all([loadStatus(), loadDevices()]);
    })
    .catch(function (error) {
      setText("login-error", "无法打开 115 设置页：" + messageOf(error));
    });
})();
