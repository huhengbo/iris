(function () {
  "use strict";

  var statusLine = document.getElementById("status-line");

  function formatTime(value) {
    if (!value) return "";
    var at = new Date(value);
    if (isNaN(at.getTime())) return String(value);
    function two(number) {
      return number < 10 ? "0" + number : String(number);
    }
    return (
      at.getFullYear() +
      "-" +
      two(at.getMonth() + 1) +
      "-" +
      two(at.getDate()) +
      " " +
      two(at.getHours()) +
      ":" +
      two(at.getMinutes())
    );
  }

  function showStatus(data) {
    if (!statusLine) return;
    if (!data || !data.api_key_set) {
      statusLine.textContent = "还没有填写 API Key";
      statusLine.className = "iris-muted";
      return;
    }
    var validation = data.validation;
    if (!validation) {
      statusLine.textContent = "还没有发起过 TMDB 请求；可以先测试连接，保存后同步时会自动更新状态。";
      statusLine.className = "iris-muted";
      return;
    }
    var message =
      validation.message ||
      (validation.status === "ok" ? "连接正常" : "TMDB 连接不可用");
    var time = formatTime(validation.checked_at);
    statusLine.textContent = message + (time ? "（" + time + "）" : "");
    statusLine.className = validation.status === "ok" ? "iris-success" : "iris-error";
  }

  function loadStatus(iris) {
    iris.json("status").then(showStatus, function (error) {
      if (!statusLine) return;
      statusLine.textContent = (error && error.message) || "读取连接状态失败";
      statusLine.className = "iris-error";
    });
  }

  IrisPlugin.ready()
    .then(function (iris) {
      loadStatus(iris);
      return iris.settingsForm(document.getElementById("settings"), {
        title: "TMDB 元数据",
        description:
          "填写 TMDB 的 API Key 或读取令牌。测试连接只验证当前输入，不会保存。",
        onSaved: function () {
          loadStatus(iris);
        },
      });
    })
    .catch(function (error) {
      IrisPlugin.toast((error && error.message) || "加载设置失败");
    });
})();
