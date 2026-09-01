from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from photo_curator import __version__
from photo_curator.acceptance import (
    AcceptanceManifestError,
    build_database_quality_evidence,
    build_manifest_template,
    build_score_snapshot,
    compare_acceptance_scores,
    evaluate_acceptance,
    format_report,
    load_manifest,
    load_score_snapshot,
)
from photo_curator.analysis.coreml_benchmark import CoreMLBenchmarkEngine, CoreMLBenchmarkError
from photo_curator.analysis.local_models import VALID_ENGINES, LocalModelEngine, LocalModelError
from photo_curator.analysis.model_registry import (
    ModelRegistryError,
    approve_model,
    get_model,
    list_models,
    register_model,
    revalidate_model,
)
from photo_curator.analysis.native_vision import (
    NativeVisionEngine,
    NativeVisionError,
    aesthetics_score_snapshot,
)
from photo_curator.analysis.performance_benchmark import (
    DEFAULT_MODES,
    run_local_model_performance_benchmark,
)
from photo_curator.db.connection import database_connection
from photo_curator.db.migrations import migrate
from photo_curator.db.repository import get_project, list_assets, list_duplicate_groups
from photo_curator.learning import (
    LearningCorpusError,
    export_learning_corpus,
    import_learning_corpus,
)
from photo_curator.logging_setup import configure_logging
from photo_curator.native_worker import run_native_worker
from photo_curator.paths import default_application_paths
from photo_curator.photokit_acceptance import run_photokit_acceptance
from photo_curator.photos.doctor import run_doctor
from photo_curator.photos.osxphotos_provider import OSXPhotosProvider
from photo_curator.release_benchmark import run_release_benchmark


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="photo-curator",
        description="Безопасный локальный помощник для ревью Apple Photos",
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=[
            "doctor",
            "version",
            "acceptance-template",
            "acceptance-score-export",
            "acceptance-evidence-export",
            "acceptance-evaluate",
            "acceptance-compare",
            "learning-corpus-export",
            "learning-corpus-import",
            "vision-benchmark",
            "coreml-benchmark",
            "local-model-performance-benchmark",
            "model-register",
            "model-list",
            "model-approve",
            "release-benchmark",
            "photokit-acceptance",
            "native-worker",
        ],
    )
    parser.add_argument("--demo", action="store_true", help="Запустить synthetic demo")
    parser.add_argument("--project-id", help="ID проекта для acceptance")
    parser.add_argument("--album-name", help="Имя одноразового PhotoKit acceptance-альбома")
    parser.add_argument(
        "--confirm-create-test-album",
        action="store_true",
        help="Разрешить создание одного реального тестового альбома Photos",
    )
    parser.add_argument("--labels", type=Path, help="JSON manifest с человеческой разметкой")
    parser.add_argument(
        "--scores",
        type=Path,
        help="Candidate JSON score snapshot; без него используется текущий selection_score",
    )
    parser.add_argument("--baseline-scores", type=Path, help="Замороженный baseline score snapshot")
    parser.add_argument(
        "--engine-name",
        default="technical-first-selection-score",
        help="Имя scorer для acceptance-score-export",
    )
    parser.add_argument(
        "--engine-version",
        default="legacy-v1",
        help="Версия scorer для acceptance-score-export",
    )
    parser.add_argument(
        "--score-field",
        choices=["selection_score", "swipe_score"],
        default="selection_score",
        help="Поле проекта для acceptance-score-export",
    )
    parser.add_argument("--min-pairwise-uplift", type=float, default=0.05)
    parser.add_argument("--min-top-k-uplift", type=float, default=0.10)
    parser.add_argument("--output", type=Path, help="Записать template в файл вместо stdout")
    parser.add_argument("--input", type=Path, help="JSON corpus для безопасного импорта")
    parser.add_argument(
        "--score-output",
        type=Path,
        help="Для vision-benchmark записать отдельный acceptance score snapshot",
    )
    parser.add_argument(
        "--model",
        type=Path,
        help="Core ML .mlmodel/.mlpackage/.mlmodelc для регистрации",
    )
    parser.add_argument("--model-id", help="ID зарегистрированной Core ML модели")
    parser.add_argument("--model-name", help="Человекочитаемое имя модели")
    parser.add_argument("--model-version", help="Неизменяемая версия модели")
    parser.add_argument("--license-id", help="SPDX или точный идентификатор лицензии")
    parser.add_argument("--source-url", help="Источник модели и лицензии")
    parser.add_argument(
        "--commercial-use-allowed",
        action="store_true",
        help="Явно подтвердить допустимость коммерческого использования",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        help="JSON compatibility/runtime/held-out evidence для model-approve",
    )
    parser.add_argument("--warmup", type=nonnegative_int, default=0)
    parser.add_argument("--iterations", type=positive_int, default=1)
    parser.add_argument(
        "--counts",
        type=positive_int_list,
        default=(100, 2_000, 5_000),
        help="Размеры synthetic inventories через запятую",
    )
    parser.add_argument(
        "--gallery-count",
        type=positive_int,
        default=50_000,
        help="Размер реальной SQLite gallery-выборки",
    )
    parser.add_argument(
        "--benchmark-modes",
        type=benchmark_modes,
        default=DEFAULT_MODES,
        help="Режимы work-batch x concurrency, например 1x1,16x1,16x2",
    )
    parser.add_argument(
        "--engines",
        type=local_model_engines,
        default=tuple(sorted(VALID_ENGINES)),
        help="Локальные движки через запятую: nima,mobileclip,musiq",
    )
    parser.add_argument(
        "--max-assets",
        type=positive_int,
        default=48,
        help="Максимум готовых preview для performance benchmark",
    )
    parser.add_argument(
        "--asset-dir",
        type=Path,
        help="Локальный каталог preview вместо project-id (не обращается к Photos)",
    )
    parser.add_argument(
        "--max-peak-rss-mib",
        type=positive_float,
        default=1024.0,
        help="Fail-closed бюджет peak RSS в MiB",
    )
    parser.add_argument("--energy-trace", type=Path, help="Сохранённый Instruments .trace")
    parser.add_argument("--energy-joules", type=positive_float)
    parser.add_argument("--max-energy-joules", type=positive_float)
    parser.add_argument("--json", action="store_true", help="Вывести acceptance-отчёт как JSON")
    return parser


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("значение должно быть неотрицательным")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("значение должно быть положительным")
    return parsed


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("значение должно быть положительным")
    return parsed


def benchmark_modes(value: str) -> tuple[tuple[int, int], ...]:
    try:
        modes = tuple(
            tuple(int(part) for part in item.lower().split("x", maxsplit=1))
            for item in value.split(",")
            if item.strip()
        )
    except ValueError as error:
        raise argparse.ArgumentTypeError("режимы должны иметь вид 1x1,16x1,16x2") from error
    if not modes or any(len(mode) != 2 for mode in modes):
        raise argparse.ArgumentTypeError("режимы должны иметь вид 1x1,16x1,16x2")
    return modes


def local_model_engines(value: str) -> tuple[str, ...]:
    engines = tuple(
        dict.fromkeys(item.strip().lower() for item in value.split(",") if item.strip())
    )
    unknown = set(engines) - VALID_ENGINES
    if not engines or unknown:
        raise argparse.ArgumentTypeError(
            "engines должны быть из: " + ", ".join(sorted(VALID_ENGINES))
        )
    return engines


def positive_int_list(value: str) -> tuple[int, ...]:
    try:
        values = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as error:
        raise argparse.ArgumentTypeError("counts должны быть целыми числами") from error
    if not values or any(item < 2 for item in values):
        raise argparse.ArgumentTypeError("каждый count должен быть не меньше 2")
    return values


def print_doctor() -> int:
    paths = default_application_paths()
    paths.ensure()
    try:
        provider = OSXPhotosProvider()
    except Exception:
        provider = None
    checks = run_doctor(provider, paths)
    for check in checks:
        print(f"[{check.status}] {check.label}: {check.detail}")
    return 1 if any(check.status == "ERROR" for check in checks) else 0


def run_acceptance_command(args: argparse.Namespace) -> int:
    if not args.project_id:
        raise AcceptanceManifestError("Укажите --project-id")
    paths = default_application_paths()
    if not paths.database.is_file():
        raise AcceptanceManifestError("База Photo Curator не найдена")
    with database_connection(paths.database) as connection:
        migrate(connection)
        try:
            get_project(connection, args.project_id)
        except KeyError as error:
            raise AcceptanceManifestError(f"Проект не найден: {args.project_id}") from error
        assets = list_assets(connection, args.project_id)
        if args.command in {"acceptance-template", "acceptance-score-export"}:
            payload = (
                build_manifest_template(args.project_id, assets)
                if args.command == "acceptance-template"
                else build_score_snapshot(
                    args.project_id,
                    assets,
                    engine_name=args.engine_name,
                    engine_version=args.engine_version,
                    score_field=getattr(args, "score_field", "selection_score"),
                )
            )
            rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                try:
                    args.output.write_text(rendered, encoding="utf-8")
                except OSError as error:
                    raise AcceptanceManifestError(
                        f"Не удалось записать template: {error}"
                    ) from error
                label = (
                    "Acceptance template"
                    if args.command == "acceptance-template"
                    else "Acceptance score snapshot"
                )
                print(f"{label}: {args.output}")
            else:
                print(rendered, end="")
            return 0
        if not args.labels:
            raise AcceptanceManifestError("Укажите --labels")
        manifest = load_manifest(args.labels)
        groups = list_duplicate_groups(connection, args.project_id)
        if args.command == "acceptance-compare":
            if not args.scores or not args.baseline_scores:
                raise AcceptanceManifestError(
                    "acceptance-compare требует --scores и --baseline-scores"
                )
            if not 0 <= args.min_pairwise_uplift <= 1 or not 0 <= args.min_top_k_uplift <= 1:
                raise AcceptanceManifestError("Uplift thresholds должны быть от 0 до 1")
            report = compare_acceptance_scores(
                manifest,
                assets,
                groups,
                project_id=args.project_id,
                candidate_snapshot=load_score_snapshot(args.scores),
                baseline_snapshot=load_score_snapshot(args.baseline_scores),
                min_pairwise_uplift=args.min_pairwise_uplift,
                min_top_k_uplift=args.min_top_k_uplift,
            )
        else:
            report = evaluate_acceptance(
                manifest,
                assets,
                groups,
                project_id=args.project_id,
                score_snapshot=load_score_snapshot(args.scores)
                if getattr(args, "scores", None)
                else None,
            )
    print(
        json.dumps(report, ensure_ascii=False, indent=2)
        if args.json or args.command == "acceptance-compare"
        else format_report(report)
    )
    return 0 if report["passed"] else 1


def run_acceptance_evidence_export_command(args: argparse.Namespace) -> int:
    """Export the current, generic and Apple-only snapshots with proven provenance."""
    if not args.project_id:
        raise AcceptanceManifestError("Укажите --project-id")
    paths = default_application_paths()
    with database_connection(paths.database) as connection:
        migrate(connection)
        try:
            get_project(connection, args.project_id)
        except KeyError as error:
            raise AcceptanceManifestError(f"Проект не найден: {args.project_id}") from error
        evidence = build_database_quality_evidence(connection, args.project_id)
    rendered = json.dumps(evidence, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Acceptance evidence: {args.output}")
    else:
        print(rendered, end="")
    return 0


def run_vision_benchmark_command(args: argparse.Namespace) -> int:
    if not args.project_id:
        raise NativeVisionError("Укажите --project-id")
    paths = default_application_paths()
    with database_connection(paths.database) as connection:
        migrate(connection)
        try:
            get_project(connection, args.project_id)
        except KeyError as error:
            raise NativeVisionError(f"Проект не найден: {args.project_id}") from error
        assets = [
            (str(asset["asset_uuid"]), Path(str(asset["review_path"])))
            for asset in list_assets(connection, args.project_id)
            if asset.get("cache_state") == "ready" and asset.get("review_path")
        ]
    if not assets:
        raise NativeVisionError("В проекте нет готовых preview для benchmark")
    report = NativeVisionEngine(paths).analyze(
        assets,
        warmup_iterations=args.warmup,
        measured_iterations=args.iterations,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Vision benchmark: {args.output}")
    else:
        print(rendered, end="")
    if args.score_output:
        snapshot = aesthetics_score_snapshot(args.project_id, report)
        args.score_output.write_text(
            json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"Vision score snapshot: {args.score_output}")
    return 0


def run_release_benchmark_command(args: argparse.Namespace) -> int:
    report = run_release_benchmark(
        counts=tuple(args.counts),
        iterations=args.iterations,
        gallery_count=getattr(args, "gallery_count", 50_000),
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Release benchmark: {args.output}")
    else:
        print(rendered, end="")
    return 0 if report["passed"] else 1


def run_coreml_benchmark_command(args: argparse.Namespace) -> int:
    if not args.project_id:
        raise CoreMLBenchmarkError("Укажите --project-id")
    if not args.model_id:
        raise CoreMLBenchmarkError("Укажите --model-id зарегистрированной модели")
    paths = default_application_paths()
    with database_connection(paths.database) as connection:
        migrate(connection)
        try:
            get_project(connection, args.project_id)
        except KeyError as error:
            raise CoreMLBenchmarkError(f"Проект не найден: {args.project_id}") from error
        try:
            model = get_model(connection, model_id=args.model_id)
        except KeyError as error:
            raise CoreMLBenchmarkError(f"Модель не найдена: {args.model_id}") from error
        if not revalidate_model(connection, args.model_id):
            raise CoreMLBenchmarkError("Файлы модели изменились после регистрации")
        assets = [
            (str(asset["asset_uuid"]), Path(str(asset["review_path"])))
            for asset in list_assets(connection, args.project_id)
            if asset.get("cache_state") == "ready" and asset.get("review_path")
        ]
    if not assets:
        raise CoreMLBenchmarkError("В проекте нет готовых preview для benchmark")
    report = CoreMLBenchmarkEngine(paths).benchmark(
        Path(str(model["model_path"])),
        assets,
        warmup_iterations=args.warmup,
        measured_iterations=args.iterations,
    )
    report["registry"] = {
        "id": model["id"],
        "name": model["name"],
        "version": model["version"],
        "sha256": model["sha256"],
        "license_id": model["license_id"],
        "commercial_use_allowed": model["commercial_use_allowed"],
        "status": model["status"],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Core ML benchmark: {args.output}")
    else:
        print(rendered, end="")
    failed = [row for row in report["assets"] if row.get("error")]
    return 1 if failed else 0


def run_local_model_performance_benchmark_command(args: argparse.Namespace) -> int:
    paths = default_application_paths()
    if args.asset_dir:
        if args.project_id:
            raise LocalModelError("Укажите только один источник: --project-id или --asset-dir")
        if not args.asset_dir.is_dir():
            raise LocalModelError(f"Каталог preview не найден: {args.asset_dir}")
        supported = {".jpg", ".jpeg", ".png", ".heic", ".tif", ".tiff"}
        assets = [
            (path.stem, path)
            for path in sorted(args.asset_dir.iterdir())
            if path.is_file() and path.suffix.lower() in supported
        ][: args.max_assets]
    else:
        if not args.project_id:
            raise LocalModelError("Укажите --project-id или --asset-dir")
        with database_connection(paths.database) as connection:
            migrate(connection)
            try:
                get_project(connection, args.project_id)
            except KeyError as error:
                raise LocalModelError(f"Проект не найден: {args.project_id}") from error
            assets = [
                (str(asset["asset_uuid"]), Path(str(asset["review_path"])))
                for asset in list_assets(connection, args.project_id)
                if asset.get("cache_state") == "ready"
                and asset.get("review_path")
                and Path(str(asset["review_path"])).is_file()
            ][: args.max_assets]
    if not assets:
        raise LocalModelError("В проекте нет готовых preview для performance benchmark")
    report = run_local_model_performance_benchmark(
        LocalModelEngine(paths),
        assets,
        engines=set(args.engines),
        modes=args.benchmark_modes,
        repetitions=args.iterations,
        max_peak_rss_bytes=int(args.max_peak_rss_mib * 1024 * 1024),
        energy_trace=args.energy_trace,
        energy_joules=args.energy_joules,
        max_energy_joules=args.max_energy_joules,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Local model performance benchmark: {args.output}")
    else:
        print(rendered, end="")
    return 0 if report["passed"] else 1


def run_model_register_command(args: argparse.Namespace) -> int:
    required = {
        "--model": args.model,
        "--model-name": args.model_name,
        "--model-version": args.model_version,
        "--license-id": args.license_id,
    }
    missing = [flag for flag, value in required.items() if not value]
    if missing:
        raise ModelRegistryError(f"Укажите {', '.join(missing)}")
    paths = default_application_paths()
    paths.ensure()
    with database_connection(paths.database) as connection:
        migrate(connection)
        model = register_model(
            connection,
            name=args.model_name,
            version=args.model_version,
            model_path=args.model,
            license_id=args.license_id,
            source_url=args.source_url,
            commercial_use_allowed=args.commercial_use_allowed,
        )
    print(json.dumps(model, ensure_ascii=False, indent=2))
    return 0


def run_model_list_command() -> int:
    paths = default_application_paths()
    paths.ensure()
    with database_connection(paths.database) as connection:
        migrate(connection)
        models = list_models(connection)
        for model in models:
            revalidate_model(connection, str(model["id"]))
        models = list_models(connection)
    print(json.dumps(models, ensure_ascii=False, indent=2))
    return 0


def run_model_approve_command(args: argparse.Namespace) -> int:
    if not args.model_id or not args.evidence:
        raise ModelRegistryError("Укажите --model-id и --evidence")
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelRegistryError("Evidence должен быть читаемым JSON") from error
    if not isinstance(evidence, dict):
        raise ModelRegistryError("Evidence должен быть JSON object")
    paths = default_application_paths()
    paths.ensure()
    with database_connection(paths.database) as connection:
        migrate(connection)
        try:
            model = approve_model(connection, args.model_id, compatibility=evidence)
        except KeyError as error:
            raise ModelRegistryError(f"Модель не найдена: {args.model_id}") from error
    print(json.dumps(model, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.error(
            "укажите команду; пользовательский интерфейс запускается через PhotoCurator.app"
        )
    if args.command == "version":
        print(__version__)
        return
    if args.command == "doctor":
        raise SystemExit(print_doctor())
    if args.command == "vision-benchmark":
        try:
            result = run_vision_benchmark_command(args)
        except (NativeVisionError, OSError) as error:
            print(f"Vision benchmark error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "coreml-benchmark":
        try:
            result = run_coreml_benchmark_command(args)
        except (CoreMLBenchmarkError, OSError, ValueError) as error:
            print(f"Core ML benchmark error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "local-model-performance-benchmark":
        try:
            result = run_local_model_performance_benchmark_command(args)
        except (LocalModelError, OSError, ValueError) as error:
            print(f"Local model performance benchmark error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "model-register":
        try:
            result = run_model_register_command(args)
        except (ModelRegistryError, OSError) as error:
            print(f"Model registry error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "model-list":
        raise SystemExit(run_model_list_command())
    if args.command == "model-approve":
        try:
            result = run_model_approve_command(args)
        except (ModelRegistryError, OSError) as error:
            print(f"Model registry error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "release-benchmark":
        raise SystemExit(run_release_benchmark_command(args))
    if args.command == "photokit-acceptance":
        if not args.confirm_create_test_album:
            parser.error("photokit-acceptance требует --confirm-create-test-album")
        if not args.project_id:
            parser.error("photokit-acceptance требует --project-id")
        try:
            report = run_photokit_acceptance(
                default_application_paths(),
                project_id=args.project_id,
                album_name=args.album_name,
            )
            rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(rendered, encoding="utf-8")
                print(f"PhotoKit acceptance: {args.output}")
            else:
                print(rendered, end="")
            result = 0 if report["passed"] else 1
        except (KeyError, OSError, RuntimeError, ValueError) as error:
            print(f"PhotoKit acceptance error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "native-worker":
        paths = default_application_paths()
        paths.ensure()
        configure_logging(paths.log_file)
        raise SystemExit(run_native_worker(paths, demo=args.demo))
    if args.command == "learning-corpus-export":
        paths = default_application_paths()
        with database_connection(paths.database) as connection:
            migrate(connection)
            corpus = export_learning_corpus(connection)
        rendered = json.dumps(corpus, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
            print(f"Learning corpus: {args.output}")
        else:
            print(rendered, end="")
        raise SystemExit(0)
    if args.command == "learning-corpus-import":
        if not args.input:
            parser.error("learning-corpus-import требует --input")
        try:
            corpus = json.loads(args.input.read_text(encoding="utf-8"))
            if not isinstance(corpus, dict):
                raise LearningCorpusError("Learning corpus root must be an object")
            paths = default_application_paths()
            with database_connection(paths.database) as connection:
                migrate(connection)
                counts = import_learning_corpus(connection, corpus)
            print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
            result = 0
        except (LearningCorpusError, OSError, json.JSONDecodeError) as error:
            print(f"Learning corpus import error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command == "acceptance-evidence-export":
        try:
            result = run_acceptance_evidence_export_command(args)
        except AcceptanceManifestError as error:
            print(f"Acceptance error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)
    if args.command in {
        "acceptance-template",
        "acceptance-score-export",
        "acceptance-evaluate",
        "acceptance-compare",
    }:
        try:
            result = run_acceptance_command(args)
        except AcceptanceManifestError as error:
            print(f"Acceptance error: {error}", file=sys.stderr)
            result = 2
        raise SystemExit(result)

    parser.error(f"неподдерживаемая команда: {args.command}")


if __name__ == "__main__":
    main(sys.argv[1:])
