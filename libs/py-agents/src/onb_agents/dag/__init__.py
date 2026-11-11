"""DAG compilation: a published template version plus a hire's variables become a pruned DAG
(PRD §5.1, §7.1 Compilation, FR-R3).

    from onb_agents.dag import compile_dag
    dag = compile_dag(template, hire_variables)

See `compiler.py` for the rules, `sla.py` for due dates and `variables.py` for how variables are checked.
"""

from .compiler import CompiledDag, CompiledTask, TaskStatus, compile_dag
from .errors import CompileError, CompileReason

__all__ = ["CompileError", "CompileReason", "CompiledDag", "CompiledTask", "TaskStatus", "compile_dag"]
