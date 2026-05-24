<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Pragma: no-cache');
header('Expires: 0');

$stateFile = __DIR__ . '/sample-export-control.json';
$allowedIntervals = [10, 60, 600, 3600];

function sample_export_interval($value, array $allowedIntervals): int
{
    $seconds = (int) $value;
    return in_array($seconds, $allowedIntervals, true) ? $seconds : 10;
}

function sample_export_default_state(): array
{
    return [
        'enabled' => true,
        'interval_seconds' => 10,
        'updated_at' => gmdate('c'),
        'source' => 'default',
    ];
}

function sample_export_read_state(string $stateFile): array
{
    if (!is_file($stateFile)) {
        return sample_export_default_state();
    }
    $raw = file_get_contents($stateFile);
    if ($raw === false || trim($raw) === '') {
        return sample_export_default_state();
    }
    $state = json_decode($raw, true);
    if (!is_array($state)) {
        return sample_export_default_state();
    }
    $state['enabled'] = array_key_exists('enabled', $state) ? (bool) $state['enabled'] : true;
    global $allowedIntervals;
    $state['interval_seconds'] = isset($state['interval_seconds'])
        ? sample_export_interval($state['interval_seconds'], $allowedIntervals)
        : 10;
    $state['updated_at'] = isset($state['updated_at']) ? (string) $state['updated_at'] : gmdate('c');
    $state['source'] = isset($state['source']) ? (string) $state['source'] : 'file';
    return $state;
}

function sample_export_bool($value): bool
{
    if (is_bool($value)) {
        return $value;
    }
    $text = strtolower(trim((string) $value));
    return in_array($text, ['1', 'true', 'yes', 'on', 'enabled'], true);
}

$state = sample_export_read_state($stateFile);
$writeOk = null;

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $raw = file_get_contents('php://input');
    $payload = json_decode($raw ?: '', true);
    if (!is_array($payload)) {
        $payload = $_POST;
    }
    if (array_key_exists('enabled', $payload)) {
        $state['enabled'] = sample_export_bool($payload['enabled']);
    }
    if (array_key_exists('interval_seconds', $payload)) {
        $state['interval_seconds'] = sample_export_interval($payload['interval_seconds'], $allowedIntervals);
    }
    $state['updated_at'] = gmdate('c');
    $state['source'] = 'avatar-ui';
    $writeOk = file_put_contents(
        $stateFile,
        json_encode($state, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL,
        LOCK_EX
    ) !== false;
}

$state['write_ok'] = $writeOk;
echo json_encode($state, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES) . PHP_EOL;
