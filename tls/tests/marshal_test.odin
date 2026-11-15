package tests

import "core:testing"
import "core:encoding/json"
import "src:server"

@(test)
marshal_strings_preserve_json_escaping :: proc(t: ^testing.T) {
	values := []string{"", "plain", "\"\\\n\r\t\b\f", "\x00\x01\x0b\x1f", "é ⚡"}
	expected := []string{`""`, `"plain"`, `"\"\\\n\r\t\b\f"`, `"\u0000\u0001\u000b\u001f"`, `"é ⚡"`}
	for value, index in values {
		data, err := server.marshal(value)
		testing.expect(t, err == nil)
		if index < 4 {
			testing.expect_value(t, string(data), expected[index])
		}
		decoded: string
		decode_error := json.unmarshal(data, &decoded)
		testing.expect(t, decode_error == nil)
		testing.expect_value(t, decoded, value)
		delete(decoded)
		delete(data)

		terminated := make([]byte, len(value) + 1)
		copy(terminated, value)
		c_value := cstring(raw_data(terminated))
		c_data, c_err := server.marshal(c_value)
		testing.expect(t, c_err == nil)
		// C strings terminate at the first embedded NUL.
		c_expected := expected[index] if index != 3 else `""`
		if index < 4 {
			testing.expect_value(t, string(c_data), c_expected)
		}
		c_decoded: string
		c_decode_error := json.unmarshal(c_data, &c_decoded)
		testing.expect(t, c_decode_error == nil)
		c_text := value if index != 3 else ""
		testing.expect_value(t, c_decoded, c_text)
		delete(c_decoded)
		delete(c_data)
		delete(terminated)
	}
}
