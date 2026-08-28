/* Preferences. Every control is bound to a dotted settings path and saved as
 * it changes; the API key is the one thing that only ever travels one way. */
(function () {
  "use strict";

  var backend = null;
  var meta = {};
  var suppress = false;
  // What the provider last told us it has. Empty until asked.
  var models = { chat: [], other: [] };
  var OTHER = "\u0000other";   // a value no model id can collide with

  var $ = function (id) { return document.getElementById(id); };
  var all = function (selector) {
    return Array.prototype.slice.call(document.querySelectorAll(selector));
  };

  function saveModel(model) {
    backend.updateSettings(JSON.stringify({"ai.model": model}), function () {
      refresh();
      toast("Model set to " + model);
    });
  }

  function toast(message) {
    var node = $("toast");
    node.textContent = message;
    node.classList.add("show");
    setTimeout(function () { node.classList.remove("show"); }, 2200);
  }

  // -- binding -----------------------------------------------------------

  function valueOf(settings, path) {
    var parts = path.split(".");
    return (settings[parts[0]] || {})[parts[1]];
  }

  function fill(settings) {
    suppress = true;
    all("[data-setting]").forEach(function (element) {
      var value = valueOf(settings, element.dataset.setting);
      if (value === undefined || value === null) value = "";
      if (element.type === "checkbox") element.checked = !!value;
      else if (Array.isArray(value)) element.value = value.join(", ");
      else element.value = value;
    });
    suppress = false;
  }

  function readControl(element) {
    if (element.type === "checkbox") return element.checked;
    if (element.type === "number") return parseFloat(element.value);
    if (element.dataset.setting === "general.trigger_words") {
      return element.value.split(",").map(function (w) { return w.trim(); })
        .filter(Boolean);
    }
    if (element.dataset.setting === "listening.input_device" ||
        element.dataset.setting === "voice.voice") {
      return element.value || null;
    }
    return element.value;
  }

  function bind() {
    all("[data-setting]").forEach(function (element) {
      var event = (element.tagName === "SELECT" || element.type === "checkbox")
        ? "change" : "change";
      element.addEventListener(event, function () {
        if (suppress) return;
        var changes = {};
        changes[element.dataset.setting] = readControl(element);
        backend.updateSettings(JSON.stringify(changes), function () {
          refresh();
          toast("Saved");
        });
      });
    });
  }

  // -- population --------------------------------------------------------

  function options(select, values, current, labels) {
    select.innerHTML = "";
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = labels ? (labels[value] || value) : value;
      if (String(value) === String(current)) option.selected = true;
      select.appendChild(option);
    });
  }

  function group(select, label, ids, current) {
    if (!ids.length) return;
    var optgroup = document.createElement("optgroup");
    optgroup.label = label;
    ids.forEach(function (id) {
      var option = document.createElement("option");
      option.value = id;
      option.textContent = id;
      if (id === current) option.selected = true;
      optgroup.appendChild(option);
    });
    select.appendChild(optgroup);
  }

  function fillModels(current) {
    var select = $("model");
    select.innerHTML = "";
    var known = models.chat.concat(models.other);
    // Whatever is configured is always in the list, even when the provider
    // has not been asked yet or refused to answer.
    if (current && known.indexOf(current) === -1) {
      group(select, "In use", [current], current);
    }
    group(select, "Chat models", models.chat, current);
    group(select, "Other models", models.other, current);

    var other = document.createElement("option");
    other.value = OTHER;
    other.textContent = "Other…";
    select.appendChild(other);

    var custom = $("model-custom");
    custom.hidden = true;
    custom.value = "";
    $("model-hint").textContent = known.length
      ? known.length + " models offered by this provider"
      : "Press Refresh to ask the provider what it offers.";
  }

  function refresh() {
    backend.getSettings(function (json) {
      var settings = JSON.parse(json);
      meta = settings._meta || {};
      fill(settings);

      // 🖖 beside the trigger word when it is exactly "computer" (D2).
      $("spock").textContent =
        (settings.general.trigger_words || []).some(function (w) {
          return String(w).toLowerCase() === "computer";
        }) ? "  🖖" : "";

      var providers = meta.providers || [];
      options($("provider"), providers.map(function (p) { return p.id; }),
              settings.ai.provider,
              providers.reduce(function (acc, p) { acc[p.id] = p.label; return acc; }, {}));

      fillModels(settings.ai.model);
      var stub = (meta.keys || {})[settings.ai.provider] || "";
      $("api-key").placeholder = stub ? "Saved: " + stub : "Not set";
      $("api-key").value = "";
      $("key-hint").textContent = meta.keychain_available
        ? "Stored in the system keychain. Never written to the settings file."
        : "No system keychain was found, so keys are kept for this session only.";

      options($("whisper-model"), meta.whisper_models || [],
              settings.listening.whisper_model);
      var voices = (meta.voices || []).slice();
      if (!voices.length) voices = [""];
      options($("voice"), voices, settings.voice.voice || "");

      var voiceHint = $("voice-hint");
      voiceHint.hidden = meta.platform !== "darwin";
      if (!voiceHint.hidden) {
        voiceHint.textContent =
          "Want a better voice? System Settings → Accessibility → Spoken " +
          "Content → System Voice → Manage Voices, then download an Enhanced " +
          "or Premium one — Serena, Daniel and Ava are good. It appears here " +
          "straight away. (Siri's own voices can't be used by any app but " +
          "Siri.)";
      }
      var devices = ["Default"].concat(meta.input_devices || []);
      options($("input-device"), devices,
              settings.listening.input_device || "Default");

      var encryption = meta.encryption || {};
      $("encryption-status").textContent = encryption.encrypted
        ? "Conversations are encrypted with " + encryption.algorithm +
          "; the key is held in the " + encryption.key_location + "."
        : "Conversations are stored in the clear on this machine.";
      var counts = meta.counts || {};
      $("counts").textContent = (counts.conversations || 0) + " conversations, " +
        (counts.messages || 0) + " messages stored.";
      $("transcript-warning").style.display =
        settings.privacy.transcript_debug ? "block" : "none";

      $("paths").textContent = "Data: " + meta.data_dir + " · Logs: " + meta.log_dir;
      $("version").textContent = "Version " + meta.version;
      $("login-item").disabled = !meta.login_item_supported;
    });
  }

  // -- actions -----------------------------------------------------------

  function wire() {
    all("#tabs button").forEach(function (tab) {
      tab.addEventListener("click", function () {
        all("#tabs button").forEach(function (t) { t.classList.remove("active"); });
        all(".pane").forEach(function (p) { p.classList.remove("active"); });
        tab.classList.add("active");
        document.querySelector('[data-pane="' + tab.dataset.tab + '"]')
          .classList.add("active");
      });
    });

    $("save-key").addEventListener("click", function () {
      var key = $("api-key").value.trim();
      if (!key) { toast("Enter a key first"); return; }
      var provider = $("provider").value;
      backend.setApiKey(provider, key, function (json) {
        var data = JSON.parse(json);
        $("api-key").value = "";
        if (!data.ok) {
          toast(data.error || "Could not save the key");
        } else if (data.durable) {
          toast("Key saved to the keychain");
        } else {
          // Better to say so now than to lose it silently overnight.
          toast("The keychain would not keep this key — it will work until " +
                "you quit. Rebuilding an unsigned app does this.");
        }
        refresh();
      });
    });

    $("clear-key").addEventListener("click", function () {
      backend.clearApiKey($("provider").value, function () {
        toast("Key removed");
        refresh();
      });
    });

    // The connection test and server detection run off the GUI thread and
    // answer through a signal, so the window stays responsive while they wait.
    backend.connectionTested.connect(function (json) {
      var result = $("connection-result");
      var data = JSON.parse(json);
      if (data.ok) {
        result.className = "result ok";
        var text = "Connected to " + data.model + " in " + data.latency_ms +
          " ms — it replied “" + (data.reply || "") + "”.";
        if (data.ignored && data.ignored.length) {
          // Newer models accept only the default temperature, so say so
          // rather than leaving a control that quietly does nothing.
          text += " This model ignores: " + data.ignored.join(", ") + ".";
        }
        result.textContent = text;
      } else {
        result.className = "result error";
        result.textContent = data.error;
      }
    });

    $("test-connection").addEventListener("click", function () {
      var result = $("connection-result");
      result.className = "result";
      result.textContent = "Testing…";
      backend.testConnection();
    });

    backend.serversDetected.connect(function (json) {
      var container = $("servers");
      {
        var servers = JSON.parse(json);
        container.innerHTML = "";
        if (!servers.length) {
          container.textContent = "Nothing answered on the usual ports " +
            "(1234 LM Studio, 11434 Ollama, 8080 llama.cpp, 8000, 5000).";
          return;
        }
        servers.forEach(function (server) {
          var node = document.createElement("div");
          node.className = "server";
          node.innerHTML = '<div class="name"></div><div class="url"></div>' +
                           '<div class="note"></div>';
          node.querySelector(".name").textContent = server.label +
            " — " + server.models.length + " model(s)";
          node.querySelector(".url").textContent = server.base_url;
          node.querySelector(".note").textContent = server.note || "";
          var use = document.createElement("button");
          use.textContent = "Use this";
          use.addEventListener("click", function () {
            var changes = {
              "ai.provider": server.kind === "ollama" ? "ollama" : "local",
              "ai.base_url": server.base_url
            };
            if (server.models.length) changes["ai.model"] = server.models[0];
            backend.updateSettings(JSON.stringify(changes), function () {
              refresh();
              toast("Using " + server.label);
            });
          });
          node.appendChild(use);
          container.appendChild(node);
        });
      }
    });

    $("detect-servers").addEventListener("click", function () {
      $("servers").textContent = "Looking on localhost…";
      backend.detectServers();
    });

    backend.modelsListed.connect(function (json) {
      var data = JSON.parse(json);
      if (!data.ok) {
        $("model-hint").textContent = "Could not ask the provider: " + data.error;
        return;
      }
      models = { chat: data.chat || [], other: data.other || [] };
      fillModels($("model").value === OTHER ? $("model-custom").value
                                            : $("model").value);
      toast(models.chat.length + models.other.length + " models found");
    });

    $("refresh-models").addEventListener("click", function () {
      $("model-hint").textContent = "Asking the provider…";
      backend.listModels();
    });

    $("model").addEventListener("change", function () {
      var custom = $("model-custom");
      if (this.value === OTHER) {
        custom.hidden = false;
        custom.focus();
        return;   // nothing to save until they type one
      }
      custom.hidden = true;
      saveModel(this.value);
    });

    $("model-custom").addEventListener("change", function () {
      if (this.value.trim()) saveModel(this.value.trim());
    });

    // Changing provider changes what the model list means.
    $("provider").addEventListener("change", function () {
      models = { chat: [], other: [] };
    });

    $("restore-prompt").addEventListener("click", function () {
      backend.restoreSystemPrompt(function (text) {
        $("system-prompt").value = text;
        toast("Default persona restored");
      });
    });

    $("preview-voice").addEventListener("click", function () {
      backend.previewVoice($("voice").value || "", function () {});
    });

    $("delete-conversations").addEventListener("click", function () {
      if (!window.confirm("Delete every stored conversation?")) return;
      backend.deleteAllConversations(function () {
        toast("Conversations deleted");
        refresh();
      });
    });

    $("delete-everything").addEventListener("click", function () {
      if (!window.confirm("Delete conversations, settings and stored keys?")) return;
      backend.deleteEverything(function () {
        toast("Everything deleted");
        refresh();
      });
    });

    $("reveal-logs").addEventListener("click", function () {
      backend.reveal("logs", function () {});
    });
    $("reveal-data").addEventListener("click", function () {
      backend.reveal("data", function () {});
    });
    $("export-settings").addEventListener("click", function () {
      backend.exportSettings(function (text) {
        navigator.clipboard.writeText(text);
        toast("Settings copied");
      });
    });
    $("factory-reset").addEventListener("click", function () {
      if (!window.confirm("Reset every setting to its default?")) return;
      backend.factoryReset(function () {
        toast("Settings reset");
        refresh();
      });
    });
  }

  new QWebChannel(qt.webChannelTransport, function (channel) {
    backend = channel.objects.backend;
    bind();
    wire();
    refresh();
  });
})();
