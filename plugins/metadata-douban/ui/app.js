(function () {
  "use strict";

  var statusLine = document.getElementById("status-line");

  function showStatus(data) {
    if (!statusLine) return;
    var parts = ["豆瓣补缺已启用"];
    if (data && data.cookie_set) {
      parts.push("已填写 Cookie");
    } else {
      parts.push("未填写 Cookie（可选，遇到 403 时再填）");
    }
    if (data && data.request_interval !== undefined) {
      parts.push("请求间隔 " + data.request_interval + " 秒");
    }
    statusLine.textContent = parts.join("；") + "。";
    statusLine.className = "iris-success";
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
        title: "豆瓣",
        description:
          "豆瓣没有开放 API，插件按 IMDb ID 核对条目页。遇到 403 或「检测到有异常请求」时，填写浏览器里的 Cookie 后重试。Cookie 保存在服务端，不会回显。",
        onSaved: function () {
          loadStatus(iris);
        },
      });
    })
    .catch(function (error) {
      IrisPlugin.toast((error && error.message) || "加载设置失败");
    });
})();
