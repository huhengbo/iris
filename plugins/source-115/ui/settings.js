(function () {
  "use strict";

  function byId(id) {
    return document.getElementById(id);
  }

  function setText(id, text) {
    var node = byId(id);
    if (node) node.textContent = text === null || text === undefined ? "" : String(text);
  }

  function formatTime(value) {
    if (!value) return "";
    var date = new Date(value);
    if (isNaN(date.getTime())) return String(value);
    return date.toLocaleString();
  }

  function renderLife(data) {
    if (!data) return;
    var api = data.api === "web" ? "网页版接口（App 版接口被拒，24 小时内改用它）" : "App 版接口";
    var parts = ["当前使用：" + api];
    parts.push(data.enabled ? "监听已开启" : "监听已关闭");
    if (data.last_polled_at) parts.push("上次检查 " + formatTime(data.last_polled_at));
    if (data.pending) parts.push("等待推送 " + data.pending + " 个目录");
    setText("life-status", parts.join(" · "));
    setText("life-error", data.last_error || "");
  }

  function loadLife(iris) {
    return iris.json("life").then(renderLife, function (error) {
      setText("life-status", "");
      setText("life-error", "读取生活事件状态失败：" + ((error && error.message) || "请稍后重试"));
    });
  }

  IrisPlugin.ready()
    .then(function (iris) {
      var form = iris.settingsForm(document.getElementById("settings"), {
        title: "播放与生活事件",
        description: "保存后立即生效，不影响已经在播放的设备。",
        onSaved: function () {
          loadLife(iris);
        },
      });
      loadLife(iris);
      return form;
    })
    .catch(function (error) {
      IrisPlugin.toast((error && error.message) || "加载设置失败");
    });
})();
