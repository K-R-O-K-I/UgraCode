import logging
import re


class RedactFamilyLink(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        redacted = re.sub(r"(/family/(?:access|enter)/)[^/\s]+", r"\1[redacted]", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True
