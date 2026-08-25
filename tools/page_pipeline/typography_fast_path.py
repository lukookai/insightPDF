# -*- coding: utf-8 -*-
"""Two-round, single-browser typography Fit/Fill fast path.

The visual-v07 policies remain source-relative and role-aware.  The expensive
part changes: every candidate for every block is synchronously measured in one
live Chromium DOM instead of writing one HTML/PDF and launching Chromium per
level.  Round one selects Fit and content alignment.  Round two validates the
selection and measures Fill candidates.  Candidate PDFs do not exist.

The module is deliberately geometry-blind: it can change only paragraph font,
line-height, wrapping, and the already-supported inner content-top offset.
SourceTextSlot and hard-anchor coordinates are never written here.
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path
import time
from typing import Any, Callable

from local_typography_fill import (
    FILL_TARGET_ABSOLUTE_GAP,
    FILL_TARGET_REMAINING_FRACTION,
    TypographyFillPolicy,
    _attempt as _fill_attempt,
    _selected_record as _fill_selected_record,
    apply_flow_fill_level,
    patch_html_fill_level,
)
from local_typography_fit import (
    LOCK_PAINT_TOLERANCE,
    PT_PER_CSS_PX,
    TypographyFitPolicy,
    _content_top_offset,
    _locked_attempt,
    _locked_record,
    apply_flow_content_top_offset,
    apply_flow_fit_level,
    patch_html_content_top_offset,
    patch_html_fit_level,
)


StateBuilder = Callable[[Any], str]
FitApplier = Callable[[Any, str, Any], Any]
OffsetApplier = Callable[[Any, str, float], Any]
FillApplier = Callable[[Any, str, Any, dict[str, Any]], Any]


def _shift_painted(measured: dict[str, Any], offset: float) -> dict[str, Any]:
    updated = copy.deepcopy(measured)
    box = list(updated.get("painted_content_bbox") or [])
    if len(box) == 4 and abs(offset) > 1e-9:
        box[1] = round(float(box[1]) + offset, 3)
        box[3] = round(float(box[3]) + offset, 3)
        updated["painted_content_bbox"] = box
    return updated


def _normalise_measurement(raw: dict[str, Any]) -> dict[str, Any]:
    rect = list(raw.pop("rect"))
    painted = list(raw.pop("painted_rect") or [])
    font_px = float(raw.pop("font_size_px") or 0.0)
    line_px = float(raw.pop("line_height_px") or 0.0)
    dom = [round(float(value) * PT_PER_CSS_PX, 3) for value in rect]
    ink = [round(float(value) * PT_PER_CSS_PX, 3)
           for value in painted] if painted else []
    return {
        **raw,
        "dom_measured_bbox": dom,
        "dom_measured_height": round(dom[3] - dom[1], 3),
        "painted_content_bbox": ink,
        "painted_content_height": round(ink[3] - ink[1], 3)
        if ink else 0.0,
        "final_font_size": round(font_px * PT_PER_CSS_PX, 3),
        "final_line_height": round(line_px * PT_PER_CSS_PX, 3),
    }


class TypographyBatchSession:
    """Keep one Chromium browser/page alive for at most two DOM rounds."""

    MAX_ROUNDS = 2

    def __init__(self, html_builder: StateBuilder, work_dir: str | Path):
        self.html_builder = html_builder
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.measurement_round_count = 0
        self.browser_launch_count = 0
        self.typography_trial_pdf_print_count = 0
        self.pdf_print_count = 0
        self.intermediate_pdf_print_count = 0
        self.final_pdf_print_count = 0
        self.dom_load_count = 0
        self.dom_capture_count = 0
        self.candidate_measurement_count = 0
        self._playwright = None
        self._browser = None
        self._page = None
        self._current_html_path: Path | None = None
        self.rounds: list[dict[str, Any]] = []
        self.pdf_prints: list[dict[str, Any]] = []
        self.dom_captures: list[dict[str, Any]] = []

    def __enter__(self) -> "TypographyBatchSession":
        from playwright.sync_api import sync_playwright

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(args=[
            "--no-sandbox", "--disable-gpu", "--allow-file-access-from-files",
        ])
        self.browser_launch_count = 1
        self._page = self._browser.new_page(
            viewport={"width": 900, "height": 1200},
            device_scale_factor=2)
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._playwright is not None:
            self._playwright.stop()

    def _require_page(self) -> Any:
        if self._page is None:
            raise RuntimeError("TypographyBatchSession is not open")
        return self._page

    def load_state(self, state: Any, *, artifact_name: str,
                   html_path: str | Path | None = None) -> Path:
        """Load one state into the persistent page without printing a PDF."""
        page = self._require_page()
        target = (Path(html_path) if html_path is not None
                  else self.work_dir / (artifact_name + ".html"))
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.html_builder(state), encoding="utf-8")
        page.goto(target.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts && document.fonts.ready")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        self.dom_load_count += 1
        self._current_html_path = target
        return target

    def capture_dom(self, state: Any | None = None, *, capture_name: str,
                    screenshot_path: str | Path,
                    html_path: str | Path | None = None) -> dict[str, Any]:
        """Capture a RenderLedger from this page; never emits a PDF."""
        if state is not None:
            self.load_state(state, artifact_name=capture_name,
                            html_path=html_path)
        elif self._current_html_path is None:
            raise RuntimeError("capture_dom requires a loaded state")
        from final_block_collision_qa import capture_dom_from_page

        snapshot = capture_dom_from_page(
            self._require_page(), screenshot_path)
        self.dom_capture_count += 1
        self.dom_captures.append({
            "capture_name": capture_name,
            "html_path": str(self._current_html_path),
            "screenshot_path": str(Path(screenshot_path).resolve()),
            "reason": "in_memory_dom_render_ledger",
            "pdf_printed": False,
        })
        return snapshot

    def print_pdf(self, state: Any, pdf_path: str | Path, *,
                  html_path: str | Path, reason: str = "final_delivery",
                  bounded_correction: bool = False) -> dict[str, Any]:
        """Print from this session, enforcing a two-print absolute ceiling."""
        if self.pdf_print_count >= 2:
            raise RuntimeError("bounded PDF print budget exceeded")
        if bounded_correction and self.final_pdf_print_count == 0:
            raise RuntimeError("bounded correction requires a first print")
        if not bounded_correction and self.final_pdf_print_count:
            raise RuntimeError("final delivery PDF was already printed")
        loaded = self.load_state(
            state, artifact_name="final_delivery", html_path=html_path)
        target = Path(pdf_path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        data = self._require_page().pdf(
            print_background=True, prefer_css_page_size=True)
        target.write_bytes(data)
        elapsed = time.perf_counter() - started
        self.pdf_print_count += 1
        self.final_pdf_print_count += 1
        event = {
            "sequence": self.pdf_print_count,
            "classification": ("bounded_formal_correction"
                               if bounded_correction else "final_delivery"),
            "reason": reason,
            "html_path": str(loaded),
            "pdf_path": str(target),
            "elapsed_seconds": round(elapsed, 6),
            "intermediate": False,
        }
        self.pdf_prints.append(event)
        return copy.deepcopy(event)

    def measure(self, state: Any, requests: list[dict[str, Any]], *,
                round_name: str) -> dict[str, dict[str, dict[str, Any]]]:
        if self.measurement_round_count >= self.MAX_ROUNDS:
            raise RuntimeError("typography measurement round budget exceeded")
        self.measurement_round_count += 1
        html_path = self.work_dir / (
            "typography_fast_round_%d_%s.html"
            % (self.measurement_round_count, round_name))
        self.load_state(state, artifact_name=html_path.stem,
                        html_path=html_path)
        raw = self._require_page().evaluate(r"""requests => {
          const paintedRect = el => {
            const painted=[];
            const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
            let node;
            while(node=walker.nextNode()){
              if(!node.data.trim()) continue;
              const range=document.createRange();
              range.selectNodeContents(node);
              for(const box of range.getClientRects()){
                if(box.width>0.01 && box.height>0.01)
                  painted.push([box.x,box.y,box.right,box.bottom]);
              }
            }
            for(const child of el.querySelectorAll('img,svg')){
              const box=child.getBoundingClientRect();
              if(box.width>0.01 && box.height>0.01)
                painted.push([box.x,box.y,box.right,box.bottom]);
            }
            return painted.length ? [
              Math.min(...painted.map(box=>box[0])),
              Math.min(...painted.map(box=>box[1])),
              Math.max(...painted.map(box=>box[2])),
              Math.max(...painted.map(box=>box[3]))] : null;
          };
          const output={};
          for(const request of requests){
            const selector=`.paragraph-block[data-flow-fragment="${
              CSS.escape(request.flow_fragment_id)}"]`;
            const el=document.querySelector(selector);
            if(!el){ output[request.flow_fragment_id]=null; continue; }
            const saved=el.style.cssText;
            const rows={};
            for(const variant of request.variants){
              el.style.fontSize=`${variant.font_size_pt}pt`;
              el.style.lineHeight=`${variant.line_height_pt}pt`;
              if(variant.wrapping==='optimized'){
                el.style.overflowWrap='anywhere';
                el.style.wordBreak='break-word';
                el.style.lineBreak='loose';
                el.style.hyphens='none';
                el.style.textWrap='pretty';
              }else{
                el.style.overflowWrap=request.base_wrapping.overflow_wrap;
                el.style.wordBreak=request.base_wrapping.word_break;
                el.style.lineBreak=request.base_wrapping.line_break;
                el.style.hyphens=request.base_wrapping.hyphens;
                el.style.textWrap=request.base_wrapping.text_wrap;
              }
              void el.offsetHeight;
              const rect=el.getBoundingClientRect();
              const cs=getComputedStyle(el);
              rows[variant.level]={
                render_id:el.dataset.renderId||'',
                flow_fragment_id:el.dataset.flowFragment||'',
                semantic_role:el.dataset.role||'body',
                render_source:el.dataset.renderSource||'',
                rect:[rect.x,rect.y,rect.right,rect.bottom],
                painted_rect:paintedRect(el),
                font_size_px:parseFloat(cs.fontSize)||0,
                line_height_px:parseFloat(cs.lineHeight)||0
              };
            }
            el.style.cssText=saved;
            output[request.flow_fragment_id]=rows;
          }
          return output;
        }""", requests)
        missing = [request["flow_fragment_id"] for request in requests
                   if raw.get(request["flow_fragment_id"]) is None]
        if missing:
            raise ValueError("fast typography DOM block missing: %s"
                             % ", ".join(missing))
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for request in requests:
            fragment = request["flow_fragment_id"]
            result[fragment] = {
                level: _normalise_measurement(copy.deepcopy(row))
                for level, row in raw[fragment].items()
            }
        count = sum(len(rows) for rows in result.values())
        self.candidate_measurement_count += count
        self.rounds.append({
            "round": self.measurement_round_count,
            "round_name": round_name,
            "html_path": str(html_path.resolve()),
            "block_count": len(requests),
            "candidate_measurement_count": count,
        })
        return result

    def summary(self, records: list[dict[str, Any]]) -> dict[str, Any]:
        corrections = [int(row.get("typography_correction_count") or 0)
                       for row in records]
        return {
            "schema_version": "fast.v03.chromium_page_session.v1",
            "strategy": (
                "one prediction -> one shared DOM Fit batch -> one shared "
                "DOM validation/Fill batch -> one final PDF print"),
            "browser_launch_count": self.browser_launch_count,
            "dom_load_count": self.dom_load_count,
            "dom_capture_count": self.dom_capture_count,
            "typography_measurement_round_count": (
                self.measurement_round_count),
            "candidate_measurement_count": self.candidate_measurement_count,
            "typography_trial_pdf_print_count": (
                self.typography_trial_pdf_print_count),
            "pdf_print_count": self.pdf_print_count,
            "intermediate_pdf_print_count": (
                self.intermediate_pdf_print_count),
            "final_pdf_print_count": self.final_pdf_print_count,
            "typography_correction_count_max": max(corrections, default=0),
            "typography_correction_count_gt1": sum(
                value > 1 for value in corrections),
            "rounds": copy.deepcopy(self.rounds),
            "dom_captures": copy.deepcopy(self.dom_captures),
            "pdf_prints": copy.deepcopy(self.pdf_prints),
        }


def _base_wrapping() -> dict[str, str]:
    return {"overflow_wrap": "anywhere", "word_break": "normal",
            "line_break": "auto", "hyphens": "none",
            "text_wrap": "wrap"}


def _fit_requests(lock_trace: dict[str, Any]) -> list[dict[str, Any]]:
    requests = []
    for candidate in lock_trace.get("records") or []:
        if not candidate.get("geometry_locked"):
            continue
        policy = TypographyFitPolicy(candidate.get("semantic_role"))
        if not policy.levels:
            continue
        source_font = float(candidate.get("source_font_size") or 0.0)
        source_line = float(candidate.get("source_line_height") or 0.0)
        requests.append({
            "flow_fragment_id": str(candidate["flow_fragment_id"]),
            "base_wrapping": _base_wrapping(),
            "variants": [{
                "level": level.fit_level,
                "font_size_pt": round(source_font * level.font_scale, 6),
                "line_height_pt": round(
                    source_line * level.line_height_scale, 6),
                "wrapping": level.wrapping,
            } for level in policy.levels],
        })
    return requests


def _predicted_fit_level(candidate: dict[str, Any]) -> str:
    measured = float(candidate.get("measured_height_before") or 0.0)
    capacity = float(candidate.get("source_height") or 0.0)
    if not measured or not capacity or measured <= capacity + 0.15:
        return "L0"
    ratio = measured / max(capacity, 0.01)
    if ratio <= 1.02:
        return "L2"
    if ratio <= 1.05:
        return "L3"
    return "L4"


def _fit_locked_state_fast(
        state: Any, lock_trace: dict[str, Any], initial_qa: dict[str, Any],
        session: TypographyBatchSession, apply_level: FitApplier,
        apply_offset: OffsetApplier) -> tuple[Any, dict[str, Any]]:
    current = copy.deepcopy(state)
    measurements = session.measure(
        current, _fit_requests(lock_trace), round_name="fit_batch")
    records = []
    for original in lock_trace.get("records") or []:
        if not original.get("geometry_locked"):
            records.append(copy.deepcopy(original))
            continue
        candidate = copy.deepcopy(original)
        fragment = str(candidate["flow_fragment_id"])
        policy = TypographyFitPolicy(candidate.get("semantic_role"))
        if not policy.levels:
            records.append({**candidate, "fit_success": False,
                            "fit_failure_reason": "role_has_no_fit_policy"})
            continue
        by_level = measurements[fragment]
        offset = _content_top_offset(candidate, by_level["L0"])
        candidate["content_top_offset"] = offset
        attempts = []
        selected = None
        selected_level = policy.levels[-1]
        for level in policy.levels:
            measured = _shift_painted(by_level[level.fit_level], offset)
            attempt = _locked_attempt(candidate, level, measured, initial_qa)
            attempt["measurement_truth"] = (
                "single_dom_batch_fit_candidate")
            attempts.append(attempt)
            if selected is None and attempt["fits_original_region"]:
                selected = attempt
                selected_level = level
        if selected is None:
            selected_level = policy.levels[-1]
        if abs(offset) > LOCK_PAINT_TOLERANCE:
            current = apply_offset(current, fragment, offset)
        current = apply_level(current, fragment, selected_level)
        record = _locked_record(candidate, attempts, selected)
        predicted = _predicted_fit_level(candidate)
        record.update({
            "predicted_fit_level": predicted,
            "typography_correction_count": int(
                predicted != selected_level.fit_level
                or abs(offset) > LOCK_PAINT_TOLERANCE),
            "measurement_round": 1,
            "candidate_levels_measured": len(attempts),
        })
        records.append(record)
    return current, {
        **{key: copy.deepcopy(value) for key, value in lock_trace.items()
           if key != "records"},
        "schema_version": "fast.v02.source_text_slot_lock.v1",
        "fit_order": ["L0", "L1", "L2", "L3", "L4"],
        "measurement_truth": "single_chromium_dom_batch_round_1",
        "records": records,
        "slot_capacity_unresolved_count": sum(
            row.get("geometry_locked") and row.get("fit_success") is False
            for row in records),
    }


def fit_locked_flows_fast(
        flows: list[dict[str, Any]], lock_trace: dict[str, Any],
        initial_qa: dict[str, Any], session: TypographyBatchSession,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _fit_locked_state_fast(
        flows, lock_trace, initial_qa, session,
        apply_flow_fit_level, apply_flow_content_top_offset)


def fit_locked_html_fast(
        html_text: str, lock_trace: dict[str, Any],
        initial_qa: dict[str, Any], session: TypographyBatchSession,
        ) -> tuple[str, dict[str, Any]]:
    def apply_level(state: str, fragment: str, level: Any) -> str:
        candidate = next(row for row in lock_trace.get("records") or []
                         if str(row.get("flow_fragment_id")) == fragment)
        return patch_html_fit_level(
            state, fragment,
            source_font_size=float(candidate["source_font_size"]),
            source_line_height=float(candidate["source_line_height"]),
            level=level)

    return _fit_locked_state_fast(
        html_text, lock_trace, initial_qa, session,
        apply_level, patch_html_content_top_offset)


def _fill_requests(lock_trace: dict[str, Any],
                   candidate_qa: dict[str, Any]) -> list[dict[str, Any]]:
    by_fragment = {str(row.get("flow_fragment_id") or ""): row
                   for row in candidate_qa.get("records") or []}
    requests = []
    for record in lock_trace.get("records") or []:
        if not record.get("geometry_locked"):
            continue
        fragment = str(record.get("flow_fragment_id") or "")
        candidate = by_fragment.get(fragment, {})
        policy = TypographyFillPolicy(record.get("semantic_role"))
        levels = policy.levels if (candidate.get("fill_candidate")
                                   and policy.eligible) else policy.levels[:1]
        if not levels:
            continue
        font = float(record.get("final_font_size")
                     or record.get("source_font_size") or 0.0)
        line = float(record.get("final_line_height")
                     or record.get("source_line_height") or 0.0)
        requests.append({
            "flow_fragment_id": fragment,
            "base_wrapping": _base_wrapping(),
            "variants": [{
                "level": level.fill_level,
                "font_size_pt": round(font * level.font_scale, 6),
                "line_height_pt": round(line * level.line_height_scale, 6),
                "wrapping": "optimized" if record.get(
                    "fit_level") != "L0" else "source",
            } for level in levels],
        })
    return requests


def _predicted_fill_level(candidate: dict[str, Any]) -> str:
    gap = float(candidate.get("occupancy_gap_before") or 0.0)
    if gap <= 0.035:
        return "F0"
    if gap <= 0.08:
        return "F1"
    if gap <= 0.14:
        return "F2"
    if gap <= 0.20:
        return "F3"
    return "F4"


def _fill_locked_state_fast(
        state: Any, lock_trace: dict[str, Any],
        candidate_qa: dict[str, Any], session: TypographyBatchSession,
        apply_level: FillApplier) -> tuple[Any, dict[str, Any]]:
    current = copy.deepcopy(state)
    requests = _fill_requests(lock_trace, candidate_qa)
    measurements = session.measure(
        current, requests, round_name="validate_and_fill_batch")
    lock_by_fragment = {
        str(row.get("flow_fragment_id") or ""): row
        for row in lock_trace.get("records") or []}
    records = []
    for candidate in candidate_qa.get("records") or []:
        fragment = str(candidate.get("flow_fragment_id") or "")
        if fragment not in measurements:
            record = _fill_selected_record(candidate, [], None)
            record["typography_correction_count"] = int(
                lock_by_fragment.get(fragment, {}).get(
                    "typography_correction_count") or 0)
            records.append(record)
            continue
        if not candidate.get("fill_candidate"):
            record = _fill_selected_record(candidate, [], None)
            record["fill_reason"] = str(
                candidate.get("candidate_decision") or "F0")
            record["typography_correction_count"] = int(
                lock_by_fragment.get(fragment, {}).get(
                    "typography_correction_count") or 0)
            records.append(record)
            continue
        policy = TypographyFillPolicy(candidate.get("semantic_role"))
        before_gap = float(candidate.get("occupancy_gap_before") or 0.0)
        target_gap = max(FILL_TARGET_ABSOLUTE_GAP,
                         before_gap * FILL_TARGET_REMAINING_FRACTION)
        attempts = []
        selected = None
        selected_level = policy.levels[0]
        collision_free = {"metrics": {"final_block_collision_count": 0},
                          "collisions": []}
        for level in policy.levels[1:]:
            measured = measurements[fragment][level.fill_level]
            rendered = {"measurements": {fragment: measured},
                        "collision_qa": collision_free}
            attempt = _fill_attempt(
                candidate, level, rendered, target_gap, policy)
            attempt["measurement_truth"] = (
                "single_dom_batch_fill_candidate")
            attempts.append(attempt)
            if not attempt["safe"]:
                break
            if attempt["fill_target_reached"]:
                selected = attempt
                selected_level = level
                break
        if selected is not None:
            current = apply_level(current, fragment, selected_level, candidate)
        record = _fill_selected_record(candidate, attempts, selected)
        predicted = _predicted_fill_level(candidate)
        prior = int(lock_by_fragment.get(fragment, {}).get(
            "typography_correction_count") or 0)
        # Fit-applied blocks are excluded from Fill candidates, so this
        # remains a single bounded correction per block.
        record.update({
            "predicted_fill_level": predicted,
            "typography_correction_count": max(
                prior, int(predicted != selected_level.fill_level)),
            "measurement_round": 2,
            "candidate_levels_measured": len(
                measurements[fragment]),
        })
        records.append(record)
    return current, {
        "schema_version": "fast.v02.local_typography_fill.v1",
        "fill_order": ["F0", "F1", "F2", "F3", "F4"],
        "measurement_truth": "single_chromium_dom_batch_round_2",
        "geometry_policy": "SourceTextSlot x/y/width/height immutable",
        "repack_policy": "fill never enters BrowserMeasuredRepack",
        "records": records,
    }


def fill_locked_flows_fast(
        flows: list[dict[str, Any]], lock_trace: dict[str, Any],
        candidate_qa: dict[str, Any], session: TypographyBatchSession,
        ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    return _fill_locked_state_fast(
        flows, lock_trace, candidate_qa, session, apply_flow_fill_level)


def fill_locked_html_fast(
        html_text: str, lock_trace: dict[str, Any],
        candidate_qa: dict[str, Any], session: TypographyBatchSession,
        ) -> tuple[str, dict[str, Any]]:
    return _fill_locked_state_fast(
        html_text, lock_trace, candidate_qa, session,
        patch_html_fill_level)


def empty_prefill_collision_qa(lock_trace: dict[str, Any]) -> dict[str, Any]:
    """Collision-free pre-Fill signal derived from immutable fitted slots.

    This is not the final gate.  The final formal PDF still runs the existing
    FinalBlockCollisionQA.  It merely avoids a pre-Fill candidate PDF print.
    """
    unresolved = int(lock_trace.get("slot_capacity_unresolved_count") or 0)
    return {
        "schema_version": "fast.v02.prefill_dom_collision_signal.v1",
        "metrics": {"final_block_collision_count": unresolved},
        "collisions": ([{"reason": "locked_slot_capacity_unresolved"}]
                       if unresolved else []),
        "measurement_truth": "batch DOM painted ink within immutable slots",
    }


def combined_fast_records(lock_trace: dict[str, Any],
                          fill_trace: dict[str, Any]) -> list[dict[str, Any]]:
    by_fragment = {str(row.get("flow_fragment_id") or ""): row
                   for row in fill_trace.get("records") or []}
    combined = []
    for fit in lock_trace.get("records") or []:
        row = copy.deepcopy(fit)
        fill = by_fragment.get(str(fit.get("flow_fragment_id") or ""))
        if fill:
            row["fill_level"] = fill.get("fill_level")
            row["fill_applied"] = bool(fill.get("fill_applied"))
            row["typography_correction_count"] = max(
                int(row.get("typography_correction_count") or 0),
                int(fill.get("typography_correction_count") or 0))
        combined.append(row)
    return combined


def write_fast_trace(path: str | Path, session: TypographyBatchSession,
                     lock_trace: dict[str, Any],
                     fill_trace: dict[str, Any]) -> dict[str, Any]:
    records = combined_fast_records(lock_trace, fill_trace)
    trace = {**session.summary(records), "records": records}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(trace, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    return trace


__all__ = [
    "TypographyBatchSession", "combined_fast_records",
    "empty_prefill_collision_qa", "fill_locked_flows_fast",
    "fill_locked_html_fast", "fit_locked_flows_fast",
    "fit_locked_html_fast", "write_fast_trace",
]
