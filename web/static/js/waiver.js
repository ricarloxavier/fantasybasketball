(() => {
    const CATEGORIES = ["FG%", "FT%", "3PTM", "PTS", "REB", "AST", "ST", "BLK", "TO"];
    const $ = (id) => document.getElementById(id);
    const ui = {
        summary: $("summaryPanel"), drops: $("dropsPanel"), recs: $("recsPanel"), run: $("runBtn"), status: $("status"),
        prefsStatus: $("prefsStatus"), save: $("savePrefsBtn"), reset: $("resetPrefsBtn"), team: $("teamKey"),
        window: $("projectionWindow"), pool: $("freeAgentPool"), dropN: $("dropCandidates"), maxN: $("maxResults"),
        roster: $("rosterToggleList"), dropText: $("dropPlayerText"), neverText: $("neverDropText"),
        curScore: $("includeCurrentScore"), news: $("includeNewsContext"), ownedGuard: $("includePercentOwnedGuard"),
        undroppable: $("respectUndroppable"), ownedThreshold: $("ownershipProtectThreshold"),
    };
    // Selections that survive a roster re-render (player keys).
    let pending = { team: "", drop: [], never: [] };

    const el = (tag, cls, text) => {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    };
    const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const pct = (p, d = 1) => `${(p * 100).toFixed(d)}%`;
    const val = (cat, v) => (cat === "FG%" || cat === "FT%") ? Number(v).toFixed(3) : Number(v).toFixed(1);
    const csv = (t) => String(t || "").split(",").map((v) => v.trim()).filter(Boolean);
    const setStatus = (msg, error = false) => { ui.status.textContent = msg; ui.status.className = error ? "status error" : "status"; };
    const setPrefsStatus = (msg, error = false) => { ui.prefsStatus.textContent = msg; ui.prefsStatus.className = error ? "status error" : "muted"; };

    function renderPuntOptions() {
        $("puntGrid").replaceChildren(...CATEGORIES.map((cat) => {
            const label = el("label", "punt-opt");
            const input = el("input"); input.type = "checkbox"; input.value = cat;
            label.append(input, el("span", "", cat));
            return label;
        }));
    }
    const selectedPunts = () => [...document.querySelectorAll(".punt-opt input:checked")].map((i) => i.value);
    const toggles = (kind) => [...ui.roster.querySelectorAll(kind === "drop" ? ".drop-toggle:checked" : ".never-drop-toggle:checked")].map((cb) => cb.dataset.playerKey);
    const hasRoster = () => ui.roster.querySelector(".roster-toggle-row") !== null;

    function renderTeamOptions(rows, selected) {
        ui.team.replaceChildren(...(rows.length ? rows : [{ team_key: pending.team, team_name: "Default team" }]).map((r) => {
            const o = el("option", "", r.team_name || r.team_key); o.value = r.team_key || ""; return o;
        }));
        const want = selected || pending.team;
        if (want) ui.team.value = want;
        if (!ui.team.value && ui.team.options.length) ui.team.selectedIndex = 0;
        pending.team = ui.team.value;
    }

    async function loadTeams() {
        const q = pending.team ? `?team_key=${encodeURIComponent(pending.team)}` : "";
        try {
            const resp = await fetch(`/api/waiver/teams${q}`);
            const payload = await resp.json();
            if (resp.ok && payload.success) { renderTeamOptions(payload.data.teams || [], payload.data.selected_team_key); return; }
            setStatus(payload.error || "Could not load teams.", true);
        } catch (err) { setStatus("Could not load teams.", true); }
        renderTeamOptions([], pending.team);
    }

    function renderRoster(roster) {
        const dropSet = new Set([...toggles("drop"), ...pending.drop]);
        const neverSet = new Set([...toggles("never"), ...pending.never]);
        if (!roster.length) { ui.roster.className = "roster-toggle-list empty"; ui.roster.textContent = "Run the analysis to load your roster."; return; }
        ui.roster.className = "roster-toggle-list";
        ui.roster.replaceChildren(...roster.map((p) => {
            const row = el("div", "roster-toggle-row"); row.dataset.playerKey = p.player_key;
            const meta = `${p.name} (${p.team || "-"} ${p.position || "-"}) · ${Number(p.percent_owned || 0).toFixed(1)}% owned${p.is_undroppable ? " · UNDROPPABLE" : ""}${p.status ? " · " + p.status : ""}${p.news ? " · " + p.news : ""}`;
            row.append(el("div", "roster-toggle-name", meta));
            const actions = el("div", "roster-toggle-actions");
            const mk = (cls, text, checked) => {
                const label = el("label", `toggle-pill ${cls === "drop-toggle" ? "drop" : "never"}`);
                const cb = el("input", cls); cb.type = "checkbox"; cb.dataset.playerKey = p.player_key; cb.checked = checked;
                label.append(cb, el("span", "", text)); return [label, cb];
            };
            const [dl, dcb] = mk("drop-toggle", "Drop", dropSet.has(p.player_key));
            const [nl, ncb] = mk("never-drop-toggle", "Never drop", !dropSet.has(p.player_key) && neverSet.has(p.player_key));
            dcb.addEventListener("change", () => { if (dcb.checked) ncb.checked = false; syncPending(); });
            ncb.addEventListener("change", () => { if (ncb.checked) dcb.checked = false; syncPending(); });
            actions.append(dl, nl); row.append(actions);
            return row;
        }));
        syncPending();
    }
    const syncPending = () => { pending.drop = toggles("drop"); pending.never = toggles("never"); };

    function collect() {
        const prefs = {
            selected_team_key: ui.team.value || pending.team || "",
            projection_window: ui.window.value, free_agent_pool: ui.pool.value, drop_candidates: ui.dropN.value, max_results: ui.maxN.value,
            punt_categories: selectedPunts(),
            selected_drop_players: hasRoster() ? toggles("drop") : pending.drop,
            selected_never_drop_players: hasRoster() ? toggles("never") : pending.never,
            drop_player_text: ui.dropText.value, never_drop_text: ui.neverText.value,
            include_current_score: ui.curScore.checked, include_news_context: ui.news.checked,
            include_percent_owned_guard: ui.ownedGuard.checked, respect_undroppable: ui.undroppable.checked,
            ownership_protect_threshold: ui.ownedThreshold.value,
        };
        pending = { team: prefs.selected_team_key, drop: [...prefs.selected_drop_players], never: [...prefs.selected_never_drop_players] };
        return prefs;
    }

    function apply(p) {
        pending = { team: p.selected_team_key || "", drop: [...(p.selected_drop_players || [])], never: [...(p.selected_never_drop_players || [])] };
        if (ui.team.options.length) ui.team.value = pending.team;
        ui.window.value = p.projection_window; ui.pool.value = p.free_agent_pool; ui.dropN.value = p.drop_candidates; ui.maxN.value = p.max_results;
        const want = new Set(p.punt_categories || []);
        document.querySelectorAll(".punt-opt input").forEach((i) => { i.checked = want.has(i.value); });
        ui.dropText.value = p.drop_player_text || ""; ui.neverText.value = p.never_drop_text || "";
        ui.curScore.checked = !!p.include_current_score; ui.news.checked = !!p.include_news_context;
        ui.ownedGuard.checked = !!p.include_percent_owned_guard; ui.undroppable.checked = !!p.respect_undroppable;
        ui.ownedThreshold.value = p.ownership_protect_threshold;
        if (hasRoster()) {
            ui.roster.querySelectorAll(".roster-toggle-row").forEach((row) => {
                const key = row.dataset.playerKey;
                row.querySelector(".drop-toggle").checked = pending.drop.includes(key);
                row.querySelector(".never-drop-toggle").checked = !pending.drop.includes(key) && pending.never.includes(key);
            });
        }
    }

    async function savePrefs(announce) {
        const prefs = collect();
        try {
            const resp = await fetch("/api/waiver/preferences", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(prefs) });
            const payload = await resp.json();
            if (!resp.ok || !payload.success) throw new Error(payload.error || `HTTP ${resp.status}`);
            apply(payload.data);
            if (announce) setPrefsStatus("Defaults saved to your account.");
        } catch (err) { setPrefsStatus(`Could not save defaults: ${err.message}`, true); }
        return prefs;
    }

    async function loadPrefs() {
        try {
            const resp = await fetch("/api/waiver/preferences");
            const payload = await resp.json();
            if (resp.ok && payload.success) { apply(payload.data); setPrefsStatus("Loaded your saved defaults."); return; }
            throw new Error(payload.error || `HTTP ${resp.status}`);
        } catch (err) { setPrefsStatus(`Using built-in defaults (${err.message}).`, true); }
    }

    function kpi(label, value, cls, note) {
        const box = el("div", "kpi"); box.append(el("div", "label", label), el("div", `value ${cls || ""}`, value));
        if (note) box.append(el("div", "muted", note));
        return box;
    }
    function table(headers, rows) {
        const wrap = el("div", "table-wrap"); const t = el("table"); const thead = el("thead"); const tr = el("tr");
        headers.forEach((h) => tr.append(el("th", "", h))); thead.append(tr);
        const tbody = el("tbody");
        rows.forEach((cells) => { const r = el("tr"); cells.forEach((c) => r.append(c instanceof Node ? c : el("td", "", c))); tbody.append(r); });
        t.append(thead, tbody); wrap.append(t); return wrap;
    }
    const td = (text, cls) => el("td", cls, text);

    function renderSummary(d) {
        const b = d.baseline, cur = d.current_score, rem = d.remaining_schedule || { rows: [] };
        const wp = b.win_probability;
        const dropMode = (d.settings && d.settings.drop_mode) || "auto";
        const windowLabel = (d.projection_window_used || "season") === "recent_2w" ? "Last 2 weeks" : "Full season";
        const kpis = el("div", "kpis");
        kpis.append(
            kpi("Matchup", `${d.my_team_name} vs ${d.opponent_team_name}`),
            kpi("Baseline win probability", pct(wp), wp >= 0.55 ? "good" : wp >= 0.45 ? "warn" : ""),
            kpi("Projected category score", `${b.deterministic_score.wins}-${b.deterministic_score.losses}`),
            kpi("Current category score", `${cur.deterministic_score.wins}-${cur.deterministic_score.losses}-${cur.deterministic_score.ties}`),
            kpi("Week", `W${d.current_week}`, "", `${d.week_start} to ${d.week_end}`),
            kpi("Days remaining", String(d.days_remaining), "", `Games source: ${d.games_source}`),
            kpi("Drop mode", dropMode.toUpperCase()), kpi("Projection window", windowLabel),
        );
        const notes = el("div", "muted", d.projection_basis === "current_score_plus_remaining_games" ? "Win probability uses current score + remaining games." : "Win probability uses remaining games only.");
        (d.model_notes || []).forEach((n) => notes.append(el("div", "", n)));
        const weak = el("div"); weak.append(el("div", "section-title", "Weak categories to attack"));
        const chips = el("div", "chips");
        if (b.my_weaknesses.length) b.my_weaknesses.forEach((c) => chips.append(el("span", "chip bad", c))); else chips.append(el("span", "chip good", "No clear weaknesses"));
        weak.append(chips);
        const odds = el("div"); odds.append(el("div", "section-title", "Baseline category win odds"));
        const grid = el("div", "cat-grid");
        CATEGORIES.forEach((cat) => {
            const c = b.categories[cat]; const box = el("div", "cat");
            box.append(el("h4", "", `${cat}${c.is_punt ? " (punt)" : ""}`), el("div", `prob ${c.prob_you_win >= 0.5 ? "good" : "bad"}`, pct(c.prob_you_win, 0)),
                el("div", "line", `You: ${val(cat, c.my_value)}`), el("div", "line", `Opp: ${val(cat, c.opp_value)}`));
            grid.append(box);
        });
        odds.append(grid);
        const current = el("div"); current.append(el("div", "section-title", "Current score by category"));
        current.append(table(["Category", "You", "Opponent", "Leader", "Margin"], CATEGORIES.map((cat) => {
            const c = cur.categories[cat] || { my_value: 0, opp_value: 0, margin: 0, winner: "-" };
            return [`${cat}${c.is_punt ? " (punt)" : ""}`, val(cat, c.my_value), val(cat, c.opp_value), c.winner, val(cat, c.margin)];
        })));
        const sched = el("div"); sched.append(el("div", "section-title", "Remaining days and players left"));
        if (rem.rows && rem.rows.length) {
            sched.append(table(["Date", `${d.my_team_name}`, `${d.opponent_team_name}`], rem.rows.map((r) => [r.date, `${r.my_count} · ${(r.my_players || []).join(", ") || "-"}`, `${r.opp_count} · ${(r.opp_players || []).join(", ") || "-"}`])));
        } else sched.append(el("div", "empty", `Schedule detail unavailable (source: ${rem.source || "unknown"}).`));
        ui.summary.replaceChildren(kpis, notes, weak, odds, current, sched);
        ui.summary.style.display = "grid";
    }

    function renderDrops(d) {
        const drops = d.drop_candidates || [];
        ui.drops.replaceChildren(el("div", "section-title", "Drop candidates evaluated"));
        if (!drops.length) { ui.drops.append(el("div", "empty", "No drop candidates available.")); }
        else {
            const list = el("div", "drop-list");
            drops.forEach((p, i) => {
                const item = el("div", "drop-item"); const left = el("div");
                left.append(el("div", "drop-name", `${i + 1}. ${p.name}`),
                    el("div", "drop-meta", `${p.team || "-"} · ${p.position || "-"} · status ${p.status || "OK"} · ${Number(p.percent_owned || 0).toFixed(1)}% owned · ${p.games_remaining} games left${p.is_undroppable ? " · UNDROPPABLE" : ""}`));
                if (p.news) left.append(el("div", "drop-meta", p.news));
                item.append(left, el("div", "drop-meta", `Drop score ${p.drop_score}`)); list.append(item);
            });
            ui.drops.append(list);
        }
        ui.drops.style.display = "grid";
    }

    function renderRecs(d) {
        const recs = d.recommendations || [];
        ui.recs.replaceChildren(el("div", "section-title", "Top add/drop recommendations"));
        if (!recs.length) { ui.recs.append(el("div", "empty", "No recommendations for this configuration.")); ui.recs.style.display = "grid"; return; }
        ui.recs.append(table(["#", "Add", "Drop", "Δ win %", "New win %", "Flips", "Main gains", "Pickup news", "Reason"], recs.map((r) => {
            const add = el("td"); add.append(el("strong", "", r.pickup_player), el("br"), el("span", "muted", `${r.pickup_team || "-"} · ${r.pickup_position || "-"} · ${r.pickup_games_remaining} games left`));
            const drop = el("td"); drop.append(el("strong", "", r.drop_player), el("br"), el("span", "muted", `${r.drop_team || "-"} · ${r.drop_position || "-"} · ${r.drop_games_remaining} games left`));
            const delta = r.delta_win_probability;
            const gains = (r.category_deltas || []).slice(0, 2).map((x) => `${x.category} ${x.delta_prob >= 0 ? "+" : ""}${pct(x.delta_prob, 0)}`).join(" · ");
            return [String(r.rank), add, drop, td(`${delta >= 0 ? "+" : ""}${pct(delta)}`, `delta ${delta > 0 ? "up" : delta < 0 ? "down" : ""}`), pct(r.new_win_probability),
                (r.flipped_categories || []).join(", ") || "-", gains || "-", r.pickup_news || r.pickup_status_full || r.pickup_status || "-", r.reason];
        })));
        ui.recs.style.display = "grid";
    }

    async function run() {
        const prefs = collect();
        const params = new URLSearchParams();
        if (prefs.selected_team_key) params.set("team_key", prefs.selected_team_key);
        params.set("projection_window", prefs.projection_window);
        params.set("free_agent_pool", prefs.free_agent_pool); params.set("drop_candidates", prefs.drop_candidates); params.set("max_results", prefs.max_results);
        params.set("punt", prefs.punt_categories.join(","));
        const drops = [...new Set([...prefs.selected_drop_players, ...csv(prefs.drop_player_text)])];
        const nevers = [...new Set([...prefs.selected_never_drop_players, ...csv(prefs.never_drop_text)])];
        params.set("drop_player", drops.join(",")); params.set("never_drop", nevers.join(","));
        ["include_current_score", "include_news_context", "include_percent_owned_guard", "respect_undroppable", "ownership_protect_threshold"].forEach((k) => params.set(k, String(prefs[k])));
        ui.run.disabled = true; setStatus("Running analysis… (Yahoo + schedule fetch, ~20s)");
        ui.summary.style.display = ui.drops.style.display = ui.recs.style.display = "none";
        await savePrefs(false);
        try {
            const resp = await fetch(`/api/waiver/analyze?${params.toString()}`);
            const payload = await resp.json();
            if (!resp.ok || !payload.success) throw new Error(payload.error || `HTTP ${resp.status}`);
            const d = payload.data;
            renderSummary(d); renderDrops(d); renderRecs(d); renderRoster(d.my_roster || []);
            if (Array.isArray(d.available_teams)) renderTeamOptions(d.available_teams, d.my_team_key || prefs.selected_team_key);
            setStatus(`Done. Generated at ${d.generated_at}.`);
        } catch (err) { setStatus(err.message || "Analysis failed.", true); }
        finally { ui.run.disabled = false; }
    }

    ui.team.addEventListener("change", () => { pending.team = ui.team.value; });
    ui.save.addEventListener("click", () => savePrefs(true));
    ui.reset.addEventListener("click", async () => {
        apply({ selected_team_key: "", projection_window: "recent_2w", free_agent_pool: 120, drop_candidates: 4, max_results: 20, punt_categories: [],
            selected_drop_players: [], selected_never_drop_players: [], drop_player_text: "", never_drop_text: "", include_current_score: true,
            include_news_context: true, include_percent_owned_guard: true, respect_undroppable: true, ownership_protect_threshold: 80 });
        await savePrefs(true);
    });
    ui.run.addEventListener("click", run);
    (async () => { renderPuntOptions(); renderRoster([]); await loadPrefs(); await loadTeams(); })();
})();
