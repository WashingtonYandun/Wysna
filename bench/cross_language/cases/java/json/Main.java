import com.fasterxml.jackson.core.JsonFactory;
import com.fasterxml.jackson.core.JsonParser;
import com.fasterxml.jackson.core.JsonToken;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

public final class Main {
    private record Row(long id, long units, boolean active) {}

    private static Row readRow(JsonParser parser) throws IOException {
        if (parser.currentToken() != JsonToken.START_OBJECT) {
            throw new IOException("expected record");
        }
        Long id = null;
        Long units = null;
        Boolean active = null;
        while (parser.nextToken() != JsonToken.END_OBJECT) {
            if (parser.currentToken() != JsonToken.FIELD_NAME) {
                throw new IOException("expected field name");
            }
            String field = parser.currentName();
            JsonToken token = parser.nextToken();
            switch (field) {
                case "id" -> id = readInteger(parser, token);
                case "units" -> units = readInteger(parser, token);
                case "active" -> {
                    if (token != JsonToken.VALUE_TRUE && token != JsonToken.VALUE_FALSE) {
                        throw new IOException("expected boolean");
                    }
                    active = token == JsonToken.VALUE_TRUE;
                }
                default -> parser.skipChildren();
            }
        }
        if (id == null || units == null || active == null) {
            throw new IOException("missing field");
        }
        return new Row(id, units, active);
    }

    private static long readInteger(JsonParser parser, JsonToken token) throws IOException {
        if (token != JsonToken.VALUE_NUMBER_INT) {
            throw new IOException("expected integer");
        }
        return parser.getLongValue();
    }

    private static List<Row> readRows(String input) throws IOException {
        List<Row> rows = new ArrayList<>();
        try (JsonParser parser = new JsonFactory().createParser(input)) {
            if (parser.nextToken() != JsonToken.START_ARRAY) {
                throw new IOException("expected array");
            }
            while (parser.nextToken() != JsonToken.END_ARRAY) {
                rows.add(readRow(parser));
            }
            if (parser.nextToken() != null) {
                throw new IOException("trailing data");
            }
        }
        return rows;
    }

    public static void main(String[] args) {
        if (args.length != 1) {
            System.exit(2);
        }
        try {
            List<Row> rows = readRows(Files.readString(Path.of(args[0])));
            int accepted = 0;
            long total = 0;
            for (Row row : rows) {
                if (row.id() > 0 && row.active() && row.units() >= 0 && row.units() <= 1000) {
                    accepted++;
                    total += row.units();
                }
            }
            System.out.println(accepted + " " + (rows.size() - accepted) + " " + total);
        } catch (IOException exception) {
            System.exit(2);
        }
    }
}
