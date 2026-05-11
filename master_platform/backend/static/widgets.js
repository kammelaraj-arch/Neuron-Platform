/* Neuron rich digital-twin widgets — vanilla JS + SVG.
 *
 * Each widget is a small constructor that takes a host DOM element
 * and exposes one method, .render(twin, deviceMeta), called whenever
 * a new SSE payload arrives. Widgets are mobile-responsive (SVG
 * scales) and dark-themed.
 *
 * Picked by twin_control id from the device's DNA:
 *   twin.heater_control      → TempGaugeWidget
 *   twin.motor_control       → MotorStatusWidget
 *   twin.weighscale_control  → ScaleReadoutWidget
 * Plus AlarmBannerWidget mounted globally on top of the page.
 */
(function (global) {
  "use strict";

  const COLORS = {
    safe:    "#1FB95A",
    warn:    "#F5A524",
    fault:   "#E5484D",
    cool:    "#3B82F6",
    track:   "#1E293B",
    text:    "#E2E8F0",
    muted:   "#64748B",
  };

  // ── Helpers ────────────────────────────────────────────────────────────────
  function svg(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    if (attrs) for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }
  function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
  function describeArc(cx, cy, r, startDeg, endDeg) {
    const polarToCartesian = (cx, cy, r, deg) => {
      const rad = (deg - 90) * Math.PI / 180;
      return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)];
    };
    const [sx, sy] = polarToCartesian(cx, cy, r, endDeg);
    const [ex, ey] = polarToCartesian(cx, cy, r, startDeg);
    const large = endDeg - startDeg <= 180 ? "0" : "1";
    return `M ${sx} ${sy} A ${r} ${r} 0 ${large} 0 ${ex} ${ey}`;
  }
  function fmt(v, dp) {
    if (v === undefined || v === null) return "—";
    const n = Number(v);
    return Number.isFinite(n) ? n.toFixed(dp ?? 1) : String(v);
  }

  // ── Temp Gauge ─────────────────────────────────────────────────────────────
  function TempGauge(host) {
    host.innerHTML = "";
    host.classList.add("widget", "widget-temp-gauge");
    const card = document.createElement("div");
    card.className = "rounded-2xl border border-slate-800 bg-slate-900/50 p-5";
    host.appendChild(card);
    const title = document.createElement("div");
    title.className = "text-xs uppercase tracking-widest text-slate-400";
    title.textContent = "Heater · Process Temperature";
    card.appendChild(title);
    const gauge = svg("svg", { viewBox: "0 0 220 140", class: "mt-2 w-full" });
    card.appendChild(gauge);

    const cx = 110, cy = 110, r = 80;
    const track = svg("path", { d: describeArc(cx, cy, r, -90, 90), stroke: COLORS.track, "stroke-width": 14, fill: "none", "stroke-linecap": "round" });
    const value = svg("path", { d: describeArc(cx, cy, r, -90, -90), stroke: COLORS.cool, "stroke-width": 14, fill: "none", "stroke-linecap": "round" });
    const setpointMark = svg("circle", { cx: cx, cy: cy - r, r: 4, fill: COLORS.warn, opacity: 0 });
    const safetyMark = svg("circle", { cx: cx, cy: cy - r, r: 4, fill: COLORS.fault, opacity: 0 });
    gauge.append(track, value, setpointMark, safetyMark);

    const valueText = svg("text", { x: cx, y: cy - 4, "text-anchor": "middle", fill: COLORS.text, "font-size": 30, "font-family": "ui-monospace,monospace", "font-weight": 700 });
    valueText.textContent = "—";
    const unit = svg("text", { x: cx, y: cy + 18, "text-anchor": "middle", fill: COLORS.muted, "font-size": 12 });
    unit.textContent = "°C";
    gauge.append(valueText, unit);

    const meta = document.createElement("div");
    meta.className = "mt-2 grid grid-cols-3 gap-2 text-xs";
    meta.innerHTML = `
      <div><div class="text-slate-500">setpoint</div><div id="sp" class="font-mono text-emerald-300">—</div></div>
      <div><div class="text-slate-500">state</div><div id="st" class="font-mono text-cyan-300">—</div></div>
      <div><div class="text-slate-500">safety max</div><div id="sm" class="font-mono text-red-300">—</div></div>`;
    card.appendChild(meta);

    function angleFor(t, lo, hi) {
      return -90 + 180 * clamp((t - lo) / (hi - lo), 0, 1);
    }

    return {
      render(twin) {
        const r0 = (twin.reported || {});
        const d0 = (twin.desired || {});
        const t = r0.process_temp_c;
        const sp = d0.setpoint_c;
        const sm = d0.safety_max_c ?? 200;
        const lo = 0, hi = Math.max(200, Number(sm) || 200);

        valueText.textContent = fmt(t, 1);
        if (Number.isFinite(Number(t))) {
          const ang = angleFor(Number(t), lo, hi);
          let color = COLORS.safe;
          if (sp && Number(t) > Number(sp) * 0.95) color = COLORS.warn;
          if (sm && Number(t) > Number(sm) * 0.9) color = COLORS.fault;
          value.setAttribute("d", describeArc(cx, cy, r, -90, ang));
          value.setAttribute("stroke", color);
        }
        if (Number.isFinite(Number(sp))) {
          const ang = angleFor(Number(sp), lo, hi);
          const rad = (ang - 90) * Math.PI / 180;
          setpointMark.setAttribute("cx", cx + r * Math.cos(rad));
          setpointMark.setAttribute("cy", cy + r * Math.sin(rad));
          setpointMark.setAttribute("opacity", 1);
        }
        if (Number.isFinite(Number(sm))) {
          const ang = angleFor(Number(sm), lo, hi);
          const rad = (ang - 90) * Math.PI / 180;
          safetyMark.setAttribute("cx", cx + r * Math.cos(rad));
          safetyMark.setAttribute("cy", cy + r * Math.sin(rad));
          safetyMark.setAttribute("opacity", 1);
        }
        meta.querySelector("#sp").textContent = fmt(sp, 1) + " °C";
        meta.querySelector("#st").textContent = r0.state || "—";
        meta.querySelector("#sm").textContent = fmt(sm, 0) + " °C";
      },
    };
  }

  // ── Motor Status ───────────────────────────────────────────────────────────
  function MotorStatus(host) {
    host.innerHTML = "";
    const card = document.createElement("div");
    card.className = "rounded-2xl border border-slate-800 bg-slate-900/50 p-5";
    host.appendChild(card);
    card.innerHTML = `
      <div class="text-xs uppercase tracking-widest text-slate-400">Motor</div>
      <div class="mt-2 flex items-center gap-3">
        <div id="dot" class="h-4 w-4 rounded-full bg-slate-600"></div>
        <div id="state" class="text-lg font-semibold tracking-tight">—</div>
        <div id="dir" class="ml-auto text-xs text-slate-400">—</div>
      </div>
      <div class="mt-3">
        <div class="flex justify-between text-[11px] text-slate-500"><span>speed</span><span id="speed-val" class="font-mono">— %</span></div>
        <div class="mt-1 h-2 w-full rounded bg-slate-800 overflow-hidden">
          <div id="speed-bar" class="h-full bg-emerald-400 transition-all" style="width:0%"></div>
        </div>
      </div>
      <div id="fault" class="mt-2 hidden rounded-md border border-red-700/40 bg-red-500/10 px-2 py-1 text-[11px] text-red-200"></div>`;
    return {
      render(twin) {
        const r0 = twin.reported || {};
        const state = r0.state || "STOPPED";
        const dot = card.querySelector("#dot");
        const map = { RUNNING: COLORS.safe, STARTING: COLORS.cool, STOPPING: COLORS.cool, STOPPED: COLORS.muted, FAULT: COLORS.fault };
        dot.style.background = map[state] || COLORS.muted;
        if (state === "RUNNING") dot.classList.add("animate-pulse"); else dot.classList.remove("animate-pulse");
        card.querySelector("#state").textContent = state;
        const speed = clamp(Number(r0.speed_pct) || 0, 0, 100);
        card.querySelector("#speed-val").textContent = fmt(speed, 0) + " %";
        card.querySelector("#speed-bar").style.width = speed + "%";
        card.querySelector("#dir").textContent = r0.direction || "fwd";
        const fault = card.querySelector("#fault");
        if (r0.fault_code) {
          fault.textContent = "fault: " + r0.fault_code;
          fault.classList.remove("hidden");
        } else fault.classList.add("hidden");
      },
    };
  }

  // ── Scale Readout ──────────────────────────────────────────────────────────
  function ScaleReadout(host) {
    host.innerHTML = "";
    const card = document.createElement("div");
    card.className = "rounded-2xl border border-slate-800 bg-slate-900/50 p-5";
    host.appendChild(card);
    card.innerHTML = `
      <div class="text-xs uppercase tracking-widest text-slate-400">Weigh Scale</div>
      <div class="mt-3 flex items-baseline gap-3">
        <div id="weight" class="text-4xl font-mono font-semibold text-emerald-200">—</div>
        <div class="text-slate-400 text-sm">g</div>
        <div id="stable-dot" class="ml-auto flex items-center gap-1 text-xs text-slate-500">
          <span class="h-2 w-2 rounded-full bg-slate-600"></span><span>—</span>
        </div>
      </div>
      <div class="mt-3 text-[11px] text-slate-500 flex justify-between"><span id="lo">lo —</span><span id="hi">hi —</span></div>
      <div class="mt-1 relative h-2 rounded bg-slate-800 overflow-hidden">
        <div id="range" class="h-full bg-cyan-500/40 absolute" style="left:0%; width:0%"></div>
        <div id="needle" class="h-full w-1 bg-emerald-300 absolute" style="left:0%"></div>
      </div>`;
    return {
      render(twin) {
        const r0 = twin.reported || {};
        const d0 = twin.desired || {};
        const w = Number(r0.stable_weight_g ?? r0.raw_weight_g);
        card.querySelector("#weight").textContent = fmt(w, 1);
        const stable = Boolean(r0.is_stable);
        const dot = card.querySelector("#stable-dot");
        dot.querySelector("span:first-child").style.background = stable ? COLORS.safe : COLORS.warn;
        dot.querySelector("span:last-child").textContent = stable ? "stable" : "settling";
        const lo = Number(d0.lo_threshold_g);
        const hi = Number(d0.hi_threshold_g);
        if (Number.isFinite(lo) && Number.isFinite(hi) && hi > lo) {
          const span = hi - lo + (hi - lo);
          const rangeStart = ((lo - lo) / span) * 100;
          const rangeWidth = ((hi - lo) / span) * 100;
          card.querySelector("#range").style.left = rangeStart + "%";
          card.querySelector("#range").style.width = rangeWidth + "%";
          card.querySelector("#lo").textContent = "lo " + fmt(lo, 0) + " g";
          card.querySelector("#hi").textContent = "hi " + fmt(hi, 0) + " g";
          if (Number.isFinite(w)) {
            const pos = clamp(((w - lo) / (hi - lo)), 0, 1) * 100;
            card.querySelector("#needle").style.left = pos + "%";
          }
        }
      },
    };
  }

  // ── Alarm Banner ───────────────────────────────────────────────────────────
  function AlarmBanner(host) {
    host.innerHTML = "";
    const banner = document.createElement("div");
    banner.className = "hidden rounded-xl border border-red-700/60 bg-red-500/10 px-4 py-2 text-sm text-red-200 flex items-center justify-between";
    host.appendChild(banner);
    return {
      render(twin) {
        const cur = twin.current || {};
        const active = Boolean(cur.alarm_active);
        if (!active) { banner.classList.add("hidden"); return; }
        banner.classList.remove("hidden");
        banner.innerHTML = `
          <div class="flex items-center gap-2">
            <span class="inline-block h-2 w-2 rounded-full bg-red-400 animate-pulse"></span>
            <span class="font-semibold">Alarm active</span>
            <span class="text-red-300/70 text-xs ml-2">interlock tripped on device</span>
          </div>
          <button class="rounded-md border border-red-400/40 px-2 py-0.5 text-xs hover:bg-red-500/20" disabled>ACK</button>`;
      },
    };
  }

  // ── Picker ─────────────────────────────────────────────────────────────────
  const PICK = {
    "twin.heater_control":     TempGauge,
    "twin.motor_control":      MotorStatus,
    "twin.weighscale_control": ScaleReadout,
  };

  global.NeuronWidgets = {
    create(twinId, host) {
      const Ctor = PICK[twinId];
      if (!Ctor) return null;
      return Ctor(host);
    },
    AlarmBanner,
  };
})(window);
