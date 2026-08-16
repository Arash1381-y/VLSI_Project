"""Application workflow for loading, validating, and analyzing one circuit."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import NoReturn

from .cell import CellLibrary, CellLibraryError
from .circuit import Circuit
from .cli import CommandLineArguments, build_argument_parser, parse_arguments
from .config import Config, ConfigError
from .experiments import (
    DEFAULT_EXPERIMENTS,
    GENERATED_ARTIFACT_FILENAMES,
    OBSOLETE_ARTIFACT_FILENAMES,
    ExperimentError,
    Experiments,
)
from .netlist import NetListParser, NetType, NetlistError
from .validation import failure_from_exception, write_validation_report


DEFAULT_OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "outputs"
ValidationStages = dict[str, str]


def run_application(argv: list[str] | None = None) -> None:
    parser = build_argument_parser()
    arguments = parse_arguments(parser, argv)
    stages = _new_validation_stages()
    config = _load_config(parser, arguments, stages)
    output_directory = _start_run_output(arguments, config.circuit_name)
    logger = logging.getLogger(__name__)
    logger.info("Configuration validation passed: %s", arguments.config)
    logger.info("Loading circuit %s", config.circuit_name)

    try:
        circuit = _load_validated_circuit(arguments, config, stages, logger)
    except (CellLibraryError, NetlistError) as error:
        error_file = _validation_error_file(error, arguments, config, stages)
        _exit_after_validation_failure(
            parser,
            arguments,
            output_directory,
            stages,
            error,
            error_file,
            config.circuit_name,
        )

    _write_successful_validation(
        output_directory,
        arguments,
        config,
        stages,
        circuit,
    )
    try:
        run_experiments(circuit, arguments.netlist, output_directory)
    except ExperimentError as error:
        logger.exception("Experiment execution failed")
        parser.exit(2, f"error: {error}\n")


def setup_logging(verbose: bool, log_path: Path | None = None) -> None:
    log_level = logging.DEBUG if verbose else logging.INFO
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path is not None:
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,
    )


def load_circuit(arguments: CommandLineArguments) -> Circuit:
    """Load all immutable circuit inputs and construct the circuit model."""

    config = Config(arguments.config)
    cell_library = CellLibrary(config.cell_library_path)
    netlist, gates, gate_cells = NetListParser(
        arguments.netlist,
        cell_library,
    ).parse()
    return Circuit(netlist, gates, gate_cells, config, cell_library)


def run_experiments(
    circuit: Circuit,
    netlist_path: Path,
    requested_output_dir: Path | None,
) -> None:
    output_directory = (
        requested_output_dir
        if requested_output_dir is not None
        else DEFAULT_OUTPUT_ROOT / circuit.config.circuit_name
    )
    Experiments(output_directory, DEFAULT_EXPERIMENTS, netlist_path).run(circuit)


def _load_config(
    parser: argparse.ArgumentParser,
    arguments: CommandLineArguments,
    stages: ValidationStages,
) -> Config:
    try:
        config = Config(arguments.config)
    except ConfigError as error:
        output_directory = _start_run_output(arguments, None)
        stages["configuration"] = "failed"
        _exit_after_validation_failure(
            parser,
            arguments,
            output_directory,
            stages,
            error,
            arguments.config,
            None,
        )
    stages["configuration"] = "passed"
    return config


def _load_validated_circuit(
    arguments: CommandLineArguments,
    config: Config,
    stages: ValidationStages,
    logger: logging.Logger,
) -> Circuit:
    cell_library = CellLibrary(config.cell_library_path)
    stages["cell_library"] = "passed"
    logger.info("Cell-library validation passed: %s", config.cell_library_path)

    netlist, gates, gate_cells = NetListParser(
        arguments.netlist,
        cell_library,
    ).parse()
    stages["netlist"] = "passed"
    logger.info("Netlist parsing and driver validation passed: %s", arguments.netlist)

    circuit = Circuit(netlist, gates, gate_cells, config, cell_library)
    stages["dag"] = "passed"
    logger.info("DAG validation passed")
    return circuit


def _write_successful_validation(
    output_directory: Path,
    arguments: CommandLineArguments,
    config: Config,
    stages: ValidationStages,
    circuit: Circuit,
) -> None:
    write_validation_report(
        output_directory,
        circuit_name=config.circuit_name,
        config_path=arguments.config,
        netlist_path=arguments.netlist,
        stages=stages,
        valid=True,
        counts=_circuit_counts(circuit),
    )


def _output_directory(
    arguments: CommandLineArguments,
    circuit_name: str | None,
) -> Path:
    if arguments.output_dir is not None:
        return arguments.output_dir
    return DEFAULT_OUTPUT_ROOT / (circuit_name or arguments.config.parent.name)


def _start_run_output(
    arguments: CommandLineArguments,
    circuit_name: str | None,
) -> Path:
    output_directory = _output_directory(arguments, circuit_name)
    _prepare_output_directory(output_directory)
    setup_logging(arguments.debug, output_directory / "run.log")
    return output_directory


def _prepare_output_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for filename in (
        *GENERATED_ARTIFACT_FILENAMES,
        *OBSOLETE_ARTIFACT_FILENAMES,
    ):
        artifact = directory / filename
        if artifact.is_file():
            artifact.unlink()


def _new_validation_stages() -> ValidationStages:
    return {
        "configuration": "not_run",
        "cell_library": "not_run",
        "netlist": "not_run",
        "dag": "not_run",
    }


def _validation_error_file(
    error: CellLibraryError | NetlistError,
    arguments: CommandLineArguments,
    config: Config,
    stages: ValidationStages,
) -> Path:
    if isinstance(error, CellLibraryError):
        stages["cell_library"] = "failed"
        return config.cell_library_path
    if stages["netlist"] == "not_run":
        stages["netlist"] = "failed"
    else:
        stages["dag"] = "failed"
    return arguments.netlist


def _circuit_counts(circuit: Circuit) -> dict[str, int]:
    return {
        "gates": len(circuit.gates),
        "nets": len(circuit.netlist),
        "primary_inputs": sum(
            net.net_type is NetType.INPUT for net in circuit.netlist.values()
        ),
        "primary_outputs": sum(
            net.net_type is NetType.OUTPUT for net in circuit.netlist.values()
        ),
    }


def _exit_after_validation_failure(
    parser: argparse.ArgumentParser,
    arguments: CommandLineArguments,
    output_directory: Path,
    stages: ValidationStages,
    error: ConfigError | CellLibraryError | NetlistError,
    error_file: Path,
    circuit_name: str | None,
) -> NoReturn:
    failure = failure_from_exception(error, error_file)
    write_validation_report(
        output_directory,
        circuit_name=circuit_name,
        config_path=arguments.config,
        netlist_path=arguments.netlist,
        stages=stages,
        valid=False,
        errors=(failure,),
    )
    logging.getLogger(__name__).error("Validation failed: %s", error)
    parser.exit(2, f"error: {error}\n")
