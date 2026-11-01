from typing import Dict


class CodegenTelemetry:
    """Telemetry tracking C99 code generation, specialized symbols, and code bloat."""
    def __init__(
        self,
        generated_functions: int = 0,
        generated_structs: int = 0,
        generated_enums: int = 0,
        generated_classes: int = 0,
        generated_c_lines: int = 0,
        generated_c_bytes: int = 0
    ) -> None:
        self.generated_functions = generated_functions
        self.generated_structs = generated_structs
        self.generated_enums = generated_enums
        self.generated_classes = generated_classes
        self.generated_c_lines = generated_c_lines
        self.generated_c_bytes = generated_c_bytes

    def to_dict(self) -> Dict[str, int]:
        return {
            "generated_functions": self.generated_functions,
            "generated_structs": self.generated_structs,
            "generated_enums": self.generated_enums,
            "generated_classes": self.generated_classes,
            "generated_c_lines": self.generated_c_lines,
            "generated_c_bytes": self.generated_c_bytes,
        }
