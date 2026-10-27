(function () {
  "use strict";

  var statusLine = document.getElementById("status-line");

  function showStatus(data) {
    if (!statusLine) return;
    if (!data || !data.api_key_set) {
      statusLine.textContent = "还没有填写 Fanart.tv API Key，保存后才会参与图片获取。";
      statusLine.className = "iris-error";
      return;
    }
    statusLine.textContent =
      "已填写 API Key" +
      (data.client_key_set ? " 和个人 Key" : "（个人 Key 未填写，可选）") +
      "。可以先测试连接，保存后立即生效。";
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
        title: "Fanart.tv",
        description:
          "在 Fanart.tv 个人页面申请 API Key。电影按 TMDB 或 IMDb 取图，剧集只认 TVDB ID；Key 保存在服务端，不会回显。",
        onSaved: function () {
          loadStatus(iris);
        },
      });
    })
    .catch(function (error) {
      IrisPlugin.toast((error && error.message) || "加载设置失败");
    });
})();
