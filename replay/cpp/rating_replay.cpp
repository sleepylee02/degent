#include <algorithm>
#include <cstdlib>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <optional>
#include <regex>
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_set>
#include <vector>

namespace {

struct Config {
    std::string input = "data/ratings_drop_processed.jsonl";
    std::string output = "outputs/stream/replay_demo/replay_input_events.jsonl";
    std::optional<int> user_id;
    std::optional<int> limit_users;
    std::optional<int> limit_events;
    std::optional<double> start_ts;
    std::optional<double> end_ts;
};

struct Event {
    long long event_id = 0;
    long long replay_order = 0;
    int user_id = 0;
    int movie_id = 0;
    double rating = 0.0;
    std::string rated_at;
    double rated_at_ts = 0.0;
};

void print_help() {
    std::cout
        << "Usage: rating_replay [options]\n\n"
        << "Options:\n"
        << "  --input PATH          ratings_drop_processed JSONL input\n"
        << "  --output PATH         replay input event JSONL output\n"
        << "  --user-id ID          include only one user\n"
        << "  --limit-users N       include first N users encountered after filters\n"
        << "  --limit-events N      keep first N events after timestamp sorting\n"
        << "  --start-rated-at ISO  include events at/after UTC ISO timestamp\n"
        << "  --end-rated-at ISO    include events at/before UTC ISO timestamp\n"
        << "  --help                show this help\n";
}

std::string require_value(int& index, int argc, char** argv, const std::string& flag) {
    if (index + 1 >= argc) {
        throw std::runtime_error("Missing value for " + flag);
    }
    ++index;
    return argv[index];
}

std::time_t timegm_portable(std::tm* tm) {
#if defined(_WIN32)
    return _mkgmtime(tm);
#else
    return timegm(tm);
#endif
}

double parse_utc_ts(const std::string& value) {
    std::string text = value;
    if (!text.empty() && text.back() == 'Z') {
        text.pop_back();
    }

    std::tm tm{};
    std::istringstream input(text);
    input >> std::get_time(&tm, "%Y-%m-%dT%H:%M:%S");
    if (input.fail()) {
        throw std::runtime_error("Invalid UTC ISO timestamp: " + value);
    }
    return static_cast<double>(timegm_portable(&tm));
}

Config parse_args(int argc, char** argv) {
    Config config;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--help" || arg == "-h") {
            print_help();
            std::exit(0);
        } else if (arg == "--input") {
            config.input = require_value(i, argc, argv, arg);
        } else if (arg == "--output") {
            config.output = require_value(i, argc, argv, arg);
        } else if (arg == "--user-id") {
            config.user_id = std::stoi(require_value(i, argc, argv, arg));
        } else if (arg == "--limit-users") {
            config.limit_users = std::stoi(require_value(i, argc, argv, arg));
        } else if (arg == "--limit-events") {
            config.limit_events = std::stoi(require_value(i, argc, argv, arg));
        } else if (arg == "--start-rated-at") {
            config.start_ts = parse_utc_ts(require_value(i, argc, argv, arg));
        } else if (arg == "--end-rated-at") {
            config.end_ts = parse_utc_ts(require_value(i, argc, argv, arg));
        } else {
            throw std::runtime_error("Unknown argument: " + arg);
        }
    }
    return config;
}

std::string json_escape(const std::string& value) {
    std::ostringstream out;
    for (char ch : value) {
        switch (ch) {
            case '\\':
                out << "\\\\";
                break;
            case '"':
                out << "\\\"";
                break;
            case '\n':
                out << "\\n";
                break;
            case '\r':
                out << "\\r";
                break;
            case '\t':
                out << "\\t";
                break;
            default:
                out << ch;
                break;
        }
    }
    return out.str();
}

std::optional<int> parse_user_id(const std::string& line) {
    static const std::regex user_re("\"userId\"\\s*:\\s*([0-9]+)");
    std::smatch match;
    if (!std::regex_search(line, match, user_re)) {
        return std::nullopt;
    }
    return std::stoi(match[1].str());
}

std::vector<Event> parse_rating_events(const std::string& line, int user_id, long long& next_event_id) {
    static const std::regex rating_re(
        "\\{\\s*\"ratedAt\"\\s*:\\s*\"([^\"]+)\"\\s*,\\s*\"movieId\"\\s*:\\s*([0-9]+)\\s*,\\s*\"rating\"\\s*:\\s*([-+]?[0-9]+(?:\\.[0-9]+)?)\\s*\\}"
    );

    std::vector<Event> events;
    auto begin = std::sregex_iterator(line.begin(), line.end(), rating_re);
    auto end = std::sregex_iterator();
    for (auto it = begin; it != end; ++it) {
        const std::smatch& match = *it;
        Event event;
        event.event_id = next_event_id++;
        event.user_id = user_id;
        event.rated_at = match[1].str();
        event.movie_id = std::stoi(match[2].str());
        event.rating = std::stod(match[3].str());
        event.rated_at_ts = parse_utc_ts(event.rated_at);
        events.push_back(std::move(event));
    }
    return events;
}

bool include_user(const Config& config, int user_id, std::set<int>& included_users) {
    if (config.user_id.has_value() && user_id != config.user_id.value()) {
        return false;
    }
    if (included_users.count(user_id)) {
        return true;
    }
    if (config.limit_users.has_value() && static_cast<int>(included_users.size()) >= config.limit_users.value()) {
        return false;
    }
    included_users.insert(user_id);
    return true;
}

bool include_event(const Config& config, const Event& event) {
    if (config.start_ts.has_value() && event.rated_at_ts < config.start_ts.value()) {
        return false;
    }
    if (config.end_ts.has_value() && event.rated_at_ts > config.end_ts.value()) {
        return false;
    }
    return true;
}

void write_events(const std::string& output_path, std::vector<Event>& events) {
    std::sort(
        events.begin(),
        events.end(),
        [](const Event& left, const Event& right) {
            if (left.rated_at_ts != right.rated_at_ts) {
                return left.rated_at_ts < right.rated_at_ts;
            }
            if (left.user_id != right.user_id) {
                return left.user_id < right.user_id;
            }
            if (left.movie_id != right.movie_id) {
                return left.movie_id < right.movie_id;
            }
            return left.event_id < right.event_id;
        }
    );

    std::filesystem::path path(output_path);
    if (path.has_parent_path()) {
        std::filesystem::create_directories(path.parent_path());
    }
    std::ofstream output(output_path);
    if (!output) {
        throw std::runtime_error("Failed to open output: " + output_path);
    }

    for (std::size_t i = 0; i < events.size(); ++i) {
        events[i].replay_order = static_cast<long long>(i);
        output << "{"
               << "\"version\":\"stream_replay_event.v1\","
               << "\"eventId\":" << events[i].event_id << ","
               << "\"replayOrder\":" << events[i].replay_order << ","
               << "\"userId\":" << events[i].user_id << ","
               << "\"movieId\":" << events[i].movie_id << ","
               << "\"rating\":" << std::fixed << std::setprecision(3) << events[i].rating << ","
               << "\"ratedAt\":\"" << json_escape(events[i].rated_at) << "\","
               << "\"ratedAtTs\":" << std::fixed << std::setprecision(3) << events[i].rated_at_ts << ","
               << "\"source\":\"ratings_drop_processed\""
               << "}\n";
    }
}

}  // namespace

int main(int argc, char** argv) {
    try {
        Config config = parse_args(argc, argv);
        std::ifstream input(config.input);
        if (!input) {
            throw std::runtime_error("Failed to open input: " + config.input);
        }

        std::vector<Event> events;
        std::set<int> included_users;
        std::string line;
        long long next_event_id = 0;
        long long input_lines = 0;

        while (std::getline(input, line)) {
            ++input_lines;
            auto user_id = parse_user_id(line);
            if (!user_id.has_value()) {
                continue;
            }
            if (!include_user(config, user_id.value(), included_users)) {
                continue;
            }

            auto user_events = parse_rating_events(line, user_id.value(), next_event_id);
            for (auto& event : user_events) {
                if (include_event(config, event)) {
                    events.push_back(std::move(event));
                }
            }
        }

        write_events(config.output, events);

        if (config.limit_events.has_value()) {
            std::ifstream generated(config.output);
            std::vector<std::string> lines;
            std::string generated_line;
            int kept = 0;
            while (kept < config.limit_events.value() && std::getline(generated, generated_line)) {
                lines.push_back(generated_line);
                ++kept;
            }
            generated.close();
            std::ofstream truncated(config.output);
            for (const auto& item : lines) {
                truncated << item << "\n";
            }
            events.resize(static_cast<std::size_t>(std::min<int>(events.size(), config.limit_events.value())));
        }

        std::cout << "input_lines=" << input_lines
                  << " users=" << included_users.size()
                  << " events=" << events.size()
                  << " output=" << config.output
                  << "\n";
        return 0;
    } catch (const std::exception& exc) {
        std::cerr << "rating_replay error: " << exc.what() << "\n";
        return 1;
    }
}
