(function () {
  "use strict";

  var statusLine = document.getElementById("status-line");

  function showStatus(data) {
    if (!statusLine) return;
    if (!data || !data.token_set) {
      statusLine.textContent = "还没有填写 API Token，保存后才能在播放器和详情页搜索字幕。";
      statusLine.className = "iris-error";
      return;
    }
    statusLine.textContent =
      "已填写 API Token；当前使用 " +
      (data.host || "api.assrt.net") +
      "，每分钟最多 " +
      (data.requests_per_minute || 5) +
      " 次请求。可以先测试连接，保存后立即生效。";
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
        title: "射手网 assrt",
        description:
          "在射手网个人中心获取 API Token。测试连接只请求配额接口，不会保存；Token 保存在服务端，不会回显。",
        onSaved: function () {
          loadStatus(iris);
        },
      });
    })
    .catch(function (error) {
      IrisPlugin.toast((error && error.message) || "加载设置失败");
    });
})();
