"""Agent-authored tool (created at runtime by create_tool). Edit with care."""

from agent_runtime.application.interfaces.tool import Tool, ToolResult
import os


class GeneratedTool(Tool):
    name = 'read_document'
    label = 'Read Document'
    default_retryable = False
    description = 'Reads content from PDF, DOCX, or TXT files.'
    parameters = {'properties': {'file_path': {'type': 'string', 'description': 'The absolute path to the document file.'}}, 'type': 'object', 'required': ['file_path']}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            file_path = params["file_path"]
            file_extension = os.path.splitext(file_path)[1].lower()

            if file_extension == ".pdf":
                try:
                    import PyPDF2
                except ImportError:
                    return ToolResult.error("PyPDF2 is not installed. Please install it using: pip install PyPDF2")
                try:
                    with open(file_path, "rb") as file:
                        reader = PyPDF2.PdfReader(file)
                        text = ""
                        for page_num in range(len(reader.pages)):
                            text += reader.pages[page_num].extract_text()
                    return ToolResult.text(text)
                except Exception as e:
                    return ToolResult.error(f"Error reading PDF: {e}")
            elif file_extension == ".docx":
                try:
                    from docx import Document
                except ImportError:
                    return ToolResult.error("python-docx is not installed. Please install it using: pip install python-docx")
                try:
                    doc = Document(file_path)
                    text = ""
                    for paragraph in doc.paragraphs:
                        text += paragraph.text + "\n"
                    return ToolResult.text(text)
                except Exception as e:
                    return ToolResult.error(f"Error reading DOCX: {e}")
            elif file_extension == ".txt":
                try:
                    with open(file_path, "r", encoding="utf-8") as file:
                        text = file.read()
                    return ToolResult.text(text)
                except Exception as e:
                    return ToolResult.error(f"Error reading TXT: {e}")
            else:
                return ToolResult.error("Unsupported file type. Only .pdf, .docx, and .txt are supported.")
        except Exception as e:  # noqa: BLE001 — never let an authored tool crash the loop
            return ToolResult.text(f"read_document failed: {type(e).__name__}: {e}", is_error=True)


def register(api, ctx):
    api.register_tool(GeneratedTool())
